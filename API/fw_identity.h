#ifndef __FW_IDENTITY_H__
#define __FW_IDENTITY_H__

#include "stm32f4xx.h"

#define FW_IDENTITY_MAGIC          0x44495746UL  /* 0x44495746 ("FWID" little-endian) */
#define FW_IDENTITY_VERSION        1UL
#define FW_IDENTITY_IMAGE_BASE     0x08000000UL
#define FW_IDENTITY_STATUS_NONE    0UL
#define FW_IDENTITY_STATUS_VALID   1UL

#define FW_IDENTITY_CMD            0x24U
#define FW_IDENTITY_FRAME          0x24U

typedef struct {
    uint32_t magic;       /* 0x44495746 ("FWID" little-endian) */
    uint32_t version;     /* 1 */
    uint32_t image_base;  /* 0x08000000 */
    uint32_t image_len;   /* bytes of the load image, rounded UP to a multiple of 4 */
    uint32_t image_crc32; /* STM32 hardware CRC-32/MPEG-2 */
    uint32_t status;      /* 0 = not computed yet, 1 = valid */
} fw_identity_t;

extern volatile fw_identity_t g_fw_identity;   /* in RAM, filled once at boot */

/* Compute firmware identity (called once at boot before scheduler starts).
 * Enables RCC AHB1 CRC clock, reads words from image_base to image_base+image_len,
 * calculates hardware CRC-32, and populates g_fw_identity. */
void FwIdentity_Compute(void);

/* Build the 0x24 reply frame carrying the six struct fields.
 * Returns 1 on success, 0 on failure. */
uint8_t FwIdentity_BuildReply(uint8_t* out, uint16_t out_cap, uint16_t* out_len);

#endif /* __FW_IDENTITY_H__ */
