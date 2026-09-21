#include "fw_identity.h"
#include "stm32f4xx_rcc.h"
#include "stm32f4xx_crc.h"

/* Linker-generated symbol for the end of the flash load region LR_IROM1 */
extern uint32_t Load$$LR$$LR_IROM1$$Limit;
extern uint32_t Load$$LR$$LR_IROM1$$Base;

volatile fw_identity_t g_fw_identity = {
    0U, 0U, 0U, 0U, 0U, 0U
};

static void PutU32Le(uint8_t* out, uint16_t* idx, uint32_t value)
{
    out[(*idx)++] = (uint8_t)(value & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 8) & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 16) & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 24) & 0xFFU);
}

void FwIdentity_Compute(void)
{
    uint32_t base;
    uint32_t limit;
    uint32_t raw_len;
    uint32_t image_len;
    uint32_t words;
    uint32_t i;
    const volatile uint32_t* p_word;
    uint32_t crc_val;

    /* Enable CRC peripheral clock on RCC AHB1 */
    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_CRC, ENABLE);

    base = (uint32_t)&Load$$LR$$LR_IROM1$$Base;
    limit = (uint32_t)&Load$$LR$$LR_IROM1$$Limit;

    if (base == 0U) {
        base = FW_IDENTITY_IMAGE_BASE;
    }

    if (limit > base) {
        raw_len = limit - base;
    } else {
        raw_len = 0U;
    }

    /* Round UP to a multiple of 4 bytes */
    image_len = (raw_len + 3U) & ~3U;

    /* Reset STM32F4 hardware CRC data register to 0xFFFFFFFF */
    CRC_ResetDR();

    words = image_len / 4U;
    p_word = (const volatile uint32_t*)base;

    /* Feed words into hardware CRC-32 unit */
    for (i = 0U; i < words; i++) {
        CRC->DR = p_word[i];
    }

    crc_val = CRC->DR;

    g_fw_identity.magic = FW_IDENTITY_MAGIC;
    g_fw_identity.version = FW_IDENTITY_VERSION;
    g_fw_identity.image_base = base;
    g_fw_identity.image_len = image_len;
    g_fw_identity.image_crc32 = crc_val;
    g_fw_identity.status = FW_IDENTITY_STATUS_VALID;
}

uint8_t FwIdentity_BuildReply(uint8_t* out, uint16_t out_cap, uint16_t* out_len)
{
    uint16_t idx;
    uint8_t crc;
    uint16_t i;

    if ((out == 0) || (out_len == 0) || (out_cap < 31U)) {
        return 0U;
    }

    out[0] = 0xAAU;
    out[1] = 0xBBU;
    out[2] = FW_IDENTITY_FRAME;
    out[3] = 0x00U;
    out[4] = 24U; /* 6 fields * 4 bytes = 24 bytes payload */
    out[5] = 0x00U;
    idx = 6U;

    PutU32Le(out, &idx, g_fw_identity.magic);
    PutU32Le(out, &idx, g_fw_identity.version);
    PutU32Le(out, &idx, g_fw_identity.image_base);
    PutU32Le(out, &idx, g_fw_identity.image_len);
    PutU32Le(out, &idx, g_fw_identity.image_crc32);
    PutU32Le(out, &idx, g_fw_identity.status);

    crc = 0U;
    for (i = 2U; i < idx; i++) {
        crc ^= out[i];
    }
    out[idx++] = crc;

    *out_len = idx;
    return 1U;
}
