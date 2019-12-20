# Released by rdb under the Unlicense (unlicense.org)
# Based on information from:
# https://www.kernel.org/doc/Documentation/input/joystick-api.txt

import concurrent.futures
import logging
import queue
import random
import threading
import time
import sys

import os, struct, array, signal
from fcntl import ioctl

import argparse
from py_overdrive_sdk.py_overdrive import Overdrive

# parse input
parser = argparse.ArgumentParser()
parser.add_argument("--car", help="id of the bluetooth car")
parser.add_argument("--js", help="joystick device")
parser.add_argument("--host", help="host of the node gateway for bluetooth communication", default='127.0.0.1')
parser.add_argument("--port", help="port of the node gateway for bluetooth communication", type=int, default=8005)
args = parser.parse_args()

# Iterate over the joystick devices.
print('Available devices:')

for fn in os.listdir('/dev/input'):
    if fn.startswith('js'):
        print('  /dev/input/%s' % (fn))

sys.stdout.flush()

# We'll store the states here.
axis_states = {}
button_states = {}

# These constants were borrowed from linux/input.h
axis_names = {
    0x00 : 'x',
    0x01 : 'y',
    0x02 : 'z',
    0x03 : 'rx',
    0x04 : 'ry',
    0x05 : 'rz',
    0x06 : 'trottle',
    0x07 : 'rudder',
    0x08 : 'wheel',
    0x09 : 'gas',
    0x0a : 'brake',
    0x10 : 'hat0x',
    0x11 : 'hat0y',
    0x12 : 'hat1x',
    0x13 : 'hat1y',
    0x14 : 'hat2x',
    0x15 : 'hat2y',
    0x16 : 'hat3x',
    0x17 : 'hat3y',
    0x18 : 'pressure',
    0x19 : 'distance',
    0x1a : 'tilt_x',
    0x1b : 'tilt_y',
    0x1c : 'tool_width',
    0x20 : 'volume',
    0x28 : 'misc',
}

button_names = {
    0x120 : 'trigger',
    0x121 : 'thumb',
    0x122 : 'thumb2',
    0x123 : 'top',
    0x124 : 'top2',
    0x125 : 'pinkie',
    0x126 : 'base',
    0x127 : 'base2',
    0x128 : 'base3',
    0x129 : 'base4',
    0x12a : 'base5',
    0x12b : 'base6',
    0x12f : 'dead',
    0x130 : 'a',
    0x131 : 'b',
    0x132 : 'c',
    0x133 : 'x',
    0x134 : 'y',
    0x135 : 'z',
    0x136 : 'tl',
    0x137 : 'tr',
    0x138 : 'tl2',
    0x139 : 'tr2',
    0x13a : 'select',
    0x13b : 'start',
    0x13c : 'mode',
    0x13d : 'thumbl',
    0x13e : 'thumbr',

    0x220 : 'dpad_up',
    0x221 : 'dpad_down',
    0x222 : 'dpad_left',
    0x223 : 'dpad_right',

    # XBox 360 controller uses these codes.
    0x2c0 : 'dpad_left',
    0x2c1 : 'dpad_right',
    0x2c2 : 'dpad_up',
    0x2c3 : 'dpad_down',
}

axis_map = []
button_map = []

# Open the joystick device.
fn = args.js
print('Opening %s...' % fn)
sys.stdout.flush()
jsdev = open(fn, 'rb')

# Get the device name.
#buf = bytearray(63)
buf = array.array('B', [0] * 64)
ioctl(jsdev, 0x80006a13 + (0x10000 * len(buf)), buf) # JSIOCGNAME(len)
js_name = buf.tobytes().rstrip(b'\x00').decode('utf-8')
print('Device name: %s' % js_name)
sys.stdout.flush()

# Get number of axes and buttons.
buf = array.array('B', [0])
ioctl(jsdev, 0x80016a11, buf) # JSIOCGAXES
num_axes = buf[0]

buf = array.array('B', [0])
ioctl(jsdev, 0x80016a12, buf) # JSIOCGBUTTONS
num_buttons = buf[0]

# Get the axis map.
buf = array.array('B', [0] * 0x40)
ioctl(jsdev, 0x80406a32, buf) # JSIOCGAXMAP

for axis in buf[:num_axes]:
    axis_name = axis_names.get(axis, 'unknown(0x%02x)' % axis)
    axis_map.append(axis_name)
    axis_states[axis_name] = 0.0

# Get the button map.
buf = array.array('H', [0] * 200)
ioctl(jsdev, 0x80406a34, buf) # JSIOCGBTNMAP

for btn in buf[:num_buttons]:
    btn_name = button_names.get(btn, 'unknown(0x%03x)' % btn)
    button_map.append(btn_name)
    button_states[btn_name] = 0

print('%d axes found: %s' % (num_axes, ', '.join(axis_map)))
print('%d buttons found: %s' % (num_buttons, ', '.join(button_map)))

sys.stdout.flush()

car = None

executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# SIGINT Handler
def sig_handler(signum, frame):
    print("SIGINT")
    event.set()
    time.sleep(0.4)
    sys.exit(0)

def js_thread(out_queue, event):
    while not event.is_set():
        evbuf = jsdev.read(8)
        if evbuf:
            print("SENDING js event")
            out_queue.put(evbuf)
    print("Stopping js event processing")
    jsdev.close()

# Main event loop
def consumer(in_queue, event):
    current_speed = 400
    current_lane = 0.0

    # let's drive!

    # init overdrive object
    print("Waiting for node Gateway")
    time.sleep(1)
    print("Starting overdrive object")
    car = Overdrive(args.host, args.port, args.car)
    #car.change_speed(current_speed, 2000)  # set car speed with speed = 400, acceleration = 2000

    max_speed = 3000
    accelerate = False
    decelerate = False
    left = False
    right = False

    while not event.is_set():
        sys.stdout.flush()
        try:
            if not in_queue.empty():
                evbuf = in_queue.get(True, 0.2)
                in_queue.task_done()
                if evbuf:
                    time_val, value, type, number = struct.unpack('IhBB', evbuf)

                    if type & 0x80:
                        print("(initial)", end="")

                    if type & 0x01:
                        button = button_map[number]
                        if button:
                            button_states[button] = value
                            if value:
                                print("%s pressed" % (button))
                                if button == "thumb":
                                    accelerate = True
                                    decelerate = False
                                elif button == "thumb2":
                                    accelerate = False
                                    decelerate = True
                                elif button == "base4":
                                    print("Exiting due to keypress. Disconnecting car")
                                    try:
                                        del car
                                    except BaseException as err:
                                        print("Error deleting car object: {0}".format(err))
                                    print("Setting ThreadPoolExecutor Event")
                                    event.set()
                                    return
                                    #os.kill(os.getpid(), signal.SIGINT)
                                    #return
                            else:
                                print("%s released" % (button))
                                if button == "thumb":
                                    accelerate = False
                                    decelerate = True
                                elif button == "thumb2":
                                    accelerate = False
                                    decelerate = True

                    if type & 0x02:
                        axis = axis_map[number]
                        if axis:
                            fvalue = value / 32767.0
                            axis_states[axis] = fvalue
                            print("%s: %.3f" % (axis, fvalue))
                            if axis == "x" and fvalue<=-1.0:
                                left=True
                                right = False
                            elif axis == "x" and fvalue>=1.0:
                                right = True
                                left = False
                            elif axis == "y":
                                if fvalue >= 0.9:
                                    print("DOWN")
                                    accelerate = False
                                    decelerate = True
                                elif fvalue <= -0.9:
                                    print("UP")
                                    accelerate = True
                                    decelerate = False
                                else:
                                    print("UP/DOWN released")
                                    accelerate = False
                                    decelerate = True
                
            else:
                time.sleep(0.2)

            if accelerate:
                current_speed += (max_speed - current_speed) / 4
                if current_speed >= max_speed:
                    current_speed = max_speed
                print("ACCELERATING: %s" %current_speed)
                car.change_speed(int(current_speed), 2000)
        
            if decelerate:
                current_speed -= current_speed / 3
                if current_speed <= 200:
                    current_speed = 0
                print("DECELERATING: %s" %current_speed)
                car.change_speed(int(current_speed), 2000)
            
            if left:
                if current_lane >= -100:
                    current_lane -= 30.0
                    print("LEFT: %s" %current_lane)
                    car.change_lane(int(current_speed), 2000, int(current_lane))

            if right:
                if current_lane <= 100:
                    current_lane += 30.0
                    print("RIGHT: %s" %current_lane)
                    car.change_lane(int(current_speed), 2000, int(current_lane))

        except ValueError:
            print("Unexpected error:", sys.exc_info()[0])

    print("Disconnecting car")
    del car
    print("EXIT")


signal.signal(signal.SIGINT, sig_handler)

# Start
pipeline = queue.Queue(maxsize=10)
event = threading.Event()
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
    executor.submit(js_thread, pipeline, event)
    executor.submit(consumer, pipeline, event)
