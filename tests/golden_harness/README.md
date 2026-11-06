# Golden packets from mouse_m908

`libusb_stub.c` replaces libusb so mouse_m908 runs without a mouse and logs
every SET_REPORT it would send. `../fixtures/m908_example_m913.log` was made
with:

```sh
git clone https://github.com/dokutan/mouse_m908 && cd mouse_m908
g++ -std=c++17 -O1 -w -I/usr/include/libusb-1.0 -D VERSION_STRING='"golden"' \
  mouse_m908.cpp include/load_config.cpp include/data.cpp include/rd_mouse.cpp \
  include/rd_mouse_wireless.cpp include/*/*.cpp -x c path/to/libusb_stub.c \
  -o mouse_m908_golden
M913_LOG=$PWD/ex.log ./mouse_m908_golden -c examples/example_m913.ini -M 913
```
