while true
do
python3 js_linux.py --car=cc1abf88c856 --port=8005 --js=/dev/input/js0 &
python3 js_linux.py --car=da2775ffba1f --port=8005 --js=/dev/input/js1 &
../node_app/node_socket_app/run.sh
done
