#ifndef USB_DESCRIPTORS_H
#define USB_DESCRIPTORS_H

#include <stdint.h>
#include <linux/usb/ch9.h>

/* USB 描述符总长度：Config(9) + Interface(9) + EP_IN(7) + EP_OUT(7) = 32 */
#define MSD_CONFIG_TOTAL_LEN    32
#define MSD_MAX_PACKET_SIZE     512

/* String Descriptor 索引 */
#define MSD_STRING_MANUFACTURER_IDX  1
#define MSD_STRING_PRODUCT_IDX       2
#define MSD_STRING_SERIAL_IDX        3

/* 获取各描述符指针和长度 */
const struct usb_device_descriptor *msd_get_device_descriptor(void);
const uint8_t *msd_get_config_descriptor(void);  /* 返回完整的 config+interface+ep */
uint16_t msd_get_config_descriptor_len(void);

/* String Descriptor 获取
 * index: 0=LangID table, 1=Manufacturer, 2=Product, 3=Serial
 * 返回 UTF-16LE 编码的字符串数据指针（不含 bLength/bDescriptorType 前缀）
 * *out_len 输出数据字节数（不含前缀 2 字节）
 * 返回 NULL 表示该 index 不存在 */
const uint8_t *msd_get_string_descriptor(int index, uint16_t *out_len);

#endif /* USB_DESCRIPTORS_H */
