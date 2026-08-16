#include "usb-descriptors.h"
#include <string.h>

/* ===== Device Descriptor ===== */
static const struct usb_device_descriptor msd_device_desc = {
    .bLength            = USB_DT_DEVICE_SIZE,       /* 18 */
    .bDescriptorType    = USB_DT_DEVICE,            /* 0x01 */
    .bcdUSB             = __cpu_to_le16(0x0200),    /* USB 2.0 */
    .bDeviceClass       = 0,                        /* 由 Interface 指定 */
    .bDeviceSubClass    = 0,
    .bDeviceProtocol    = 0,
    .bMaxPacketSize0    = 64,                       /* EP0 max packet */
    .idVendor           = __cpu_to_le16(0x1D6B),    /* Linux Foundation */
    .idProduct          = __cpu_to_le16(0x0105),    /* Multifunction Composite Gadget */
    .bcdDevice          = __cpu_to_le16(0x0100),    /* v1.0 */
    .iManufacturer      = MSD_STRING_MANUFACTURER_IDX,
    .iProduct           = MSD_STRING_PRODUCT_IDX,
    .iSerialNumber      = MSD_STRING_SERIAL_IDX,
    .bNumConfigurations = 1,
};

/* ===== Interface Descriptor ===== */
static const struct usb_interface_descriptor msd_interface_desc = {
    .bLength            = USB_DT_INTERFACE_SIZE,    /* 9 */
    .bDescriptorType    = USB_DT_INTERFACE,         /* 0x04 */
    .bInterfaceNumber   = 0,
    .bAlternateSetting  = 0,
    .bNumEndpoints      = 2,                        /* Bulk IN + Bulk OUT */
    .bInterfaceClass    = 0x08,                     /* USB_CLASS_MASS_STORAGE */
    .bInterfaceSubClass = 0x06,                     /* SCSI transparent command set */
    .bInterfaceProtocol = 0x50,                     /* Bulk-Only Transport */
    .iInterface         = 0,                        /* 无字符串 */
};

/* ===== Endpoint Descriptors ===== */
static const struct usb_endpoint_descriptor msd_ep_in_desc = {
    .bLength            = USB_DT_ENDPOINT_SIZE,     /* 7 */
    .bDescriptorType    = USB_DT_ENDPOINT,          /* 0x05 */
    .bEndpointAddress   = 0x81,                     /* EP1 IN */
    .bmAttributes       = USB_ENDPOINT_XFER_BULK,   /* 0x02 */
    .wMaxPacketSize     = __cpu_to_le16(MSD_MAX_PACKET_SIZE), /* 512 */
    .bInterval          = 0,                        /* Bulk 端点忽略 */
};

static const struct usb_endpoint_descriptor msd_ep_out_desc = {
    .bLength            = USB_DT_ENDPOINT_SIZE,     /* 7 */
    .bDescriptorType    = USB_DT_ENDPOINT,          /* 0x05 */
    .bEndpointAddress   = 0x02,                     /* EP2 OUT */
    .bmAttributes       = USB_ENDPOINT_XFER_BULK,   /* 0x02 */
    .wMaxPacketSize     = __cpu_to_le16(MSD_MAX_PACKET_SIZE), /* 512 */
    .bInterval          = 0,                        /* Bulk 端点忽略 */
};

/* ===== Configuration Descriptor (拼合 config+interface+endpoints) ===== */
static const struct usb_config_descriptor msd_config_hdr = {
    .bLength            = USB_DT_CONFIG_SIZE,       /* 9 */
    .bDescriptorType    = USB_DT_CONFIG,            /* 0x02 */
    .wTotalLength       = __cpu_to_le16(MSD_CONFIG_TOTAL_LEN), /* 32 */
    .bNumInterfaces     = 1,
    .bConfigurationValue= 1,
    .iConfiguration     = 0,
    .bmAttributes       = 0x80,                     /* Bus-powered, bit7 reserved=1 */
    .bMaxPower          = 0x32,                     /* 100mA (50 × 2mA) */
};

/* 完整 config 描述符缓冲区（config + interface + ep_in + ep_out） */
static uint8_t msd_config_buf[MSD_CONFIG_TOTAL_LEN];

/* 初始化拼合 config 描述符（首次调用时构造）
 *
 * 注意：usb_endpoint_descriptor 的 sizeof 是 9（含音频用 bRefresh/bSynchAddress），
 * 但 USB 协议传输时 bulk/interrupt 端点只发 7 字节（USB_DT_ENDPOINT_SIZE），
 * 因此使用 USB_DT_ENDPOINT_SIZE 而不是 sizeof()。
 */
static void msd_build_config_buf(void)
{
    static int built = 0;
    if (built) return;

    size_t off = 0;
    memcpy(msd_config_buf + off, &msd_config_hdr, USB_DT_CONFIG_SIZE);
    off += USB_DT_CONFIG_SIZE;
    memcpy(msd_config_buf + off, &msd_interface_desc, USB_DT_INTERFACE_SIZE);
    off += USB_DT_INTERFACE_SIZE;
    memcpy(msd_config_buf + off, &msd_ep_in_desc, USB_DT_ENDPOINT_SIZE);
    off += USB_DT_ENDPOINT_SIZE;
    memcpy(msd_config_buf + off, &msd_ep_out_desc, USB_DT_ENDPOINT_SIZE);

    built = 1;
}

/* ===== String Descriptors ===== */

/* LangID table: 仅美式英语 0x0409 */
static const uint8_t str_langid[] = {
    0x04,       /* bLength */
    0x03,       /* bDescriptorType = STRING */
    0x09, 0x04, /* wLANGID[0] = 0x0409 */
};

/* Helper: ASCII → UTF-16LE，生成 string descriptor body（不含 bLength/bDescriptorType） */
#define STR_DESC(name, text) \
    static uint8_t str_##name[2 + sizeof(text) * 2 - 2]; \
    static void build_str_##name(void) { \
        static int done = 0; \
        if (done) return; \
        size_t i; \
        for (i = 0; i < sizeof(text) - 1; i++) { \
            str_##name[0 + i * 2] = (uint8_t)text[i]; \
            str_##name[1 + i * 2] = 0x00; \
        } \
        done = 1; \
    }

STR_DESC(manufacturer, "Lechao")
STR_DESC(product,      "Pi02W Fault Inject")
STR_DESC(serial,       "FI0001")

/* ===== 公开 API ===== */

const struct usb_device_descriptor *msd_get_device_descriptor(void)
{
    return &msd_device_desc;
}

const uint8_t *msd_get_config_descriptor(void)
{
    msd_build_config_buf();
    return msd_config_buf;
}

uint16_t msd_get_config_descriptor_len(void)
{
    return MSD_CONFIG_TOTAL_LEN;
}

const uint8_t *msd_get_string_descriptor(int index, uint16_t *out_len)
{
    switch (index) {
    case 0:
        *out_len = sizeof(str_langid) - 2; /* 不含 bLength/bDescriptorType 前缀 */
        return str_langid + 2;
    case MSD_STRING_MANUFACTURER_IDX:
        build_str_manufacturer();
        *out_len = sizeof("Lechao") - 1;   /* 字符数 */
        *out_len *= 2;                      /* UTF-16LE 每字符 2 字节 */
        return str_manufacturer;
    case MSD_STRING_PRODUCT_IDX:
        build_str_product();
        *out_len = (sizeof("Pi02W Fault Inject") - 1) * 2;
        return str_product;
    case MSD_STRING_SERIAL_IDX:
        build_str_serial();
        *out_len = (sizeof("FI0001") - 1) * 2;
        return str_serial;
    default:
        return NULL;
    }
}
