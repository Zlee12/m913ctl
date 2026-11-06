/* Stub libusb: logs every SET_REPORT to M913_LOG (hex, one packet per line). */
#include <libusb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static libusb_device_handle *fake = (libusb_device_handle *)0x1;
static FILE *logf(void){ static FILE *f; if(!f){ const char *p=getenv("M913_LOG"); f = p? fopen(p,"w"):stderr; } return f; }
int libusb_init(libusb_context **c){ (void)c; return 0; }
void libusb_exit(libusb_context *c){ (void)c; FILE*f=logf(); if(f!=stderr) fflush(f); }
libusb_device_handle *libusb_open_device_with_vid_pid(libusb_context *c, uint16_t v, uint16_t p){ (void)c;(void)v;(void)p; return fake; }
ssize_t libusb_get_device_list(libusb_context *c, libusb_device ***l){ (void)c; *l = calloc(2,sizeof(void*)); (*l)[0]=(libusb_device*)0x2; return 1; }
void libusb_free_device_list(libusb_device **l, int u){ (void)u; free(l); }
int libusb_get_device_descriptor(libusb_device *d, struct libusb_device_descriptor *s){ (void)d; memset(s,0,sizeof *s); const char *p=getenv("M913_PID"); s->idVendor=0x25a7; s->idProduct= p? (uint16_t)strtoul(p,0,16):0xfa08; return 0; }
uint8_t libusb_get_bus_number(libusb_device *d){ (void)d; return 0; }
uint8_t libusb_get_device_address(libusb_device *d){ (void)d; return 0; }
int libusb_open(libusb_device *d, libusb_device_handle **h){ (void)d; *h = fake; return 0; }
int libusb_kernel_driver_active(libusb_device_handle *h, int i){ (void)h;(void)i; return 0; }
int libusb_detach_kernel_driver(libusb_device_handle *h, int i){ (void)h;(void)i; return 0; }
int libusb_attach_kernel_driver(libusb_device_handle *h, int i){ (void)h;(void)i; return 0; }
int libusb_claim_interface(libusb_device_handle *h, int i){ (void)h;(void)i; return 0; }
int libusb_release_interface(libusb_device_handle *h, int i){ (void)h;(void)i; return 0; }
int libusb_control_transfer(libusb_device_handle *h, uint8_t rt, uint8_t rq, uint16_t v, uint16_t idx, unsigned char *d, uint16_t len, unsigned int to){
  (void)h;(void)to; FILE *f=logf();
  fprintf(f, "%02x %02x %04x %04x:", rt, rq, v, idx);
  for(int i=0;i<len;i++) fprintf(f," %02x", d[i]); fprintf(f,"\n"); fflush(f); return 0; }
int libusb_interrupt_transfer(libusb_device_handle *h, unsigned char ep, unsigned char *d, int len, int *t, unsigned int to){
  (void)h;(void)ep;(void)to; memset(d,0,len); if(t)*t=len; return 0; }
