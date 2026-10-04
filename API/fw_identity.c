/**
 * @module     fw_identity.c
 * @subsystem  telemetry
 * @owner      USER/main.c calls FwIdentity_Compute() once at start-up; subscribe.c calls FwIdentity_BuildReply()
 *             when the GS sends FW_IDENTITY_CMD.
 * @purpose    Fingerprint the running image: hardware CRC-32 over the flash load region LR_IROM1, reported to the
 *             GS so it can match the board against a known build.
 * @inputs     the Keil linker symbols Load$$LR$$LR_IROM1$$Base / $$Limit, the flash image itself.
 * @outputs    g_fw_identity; the FW_IDENTITY_FRAME reply (0xAA 0xBB id 0 len 0, six u32 LE fields, XOR checksum).
 */

#include "fw_identity.h"
#include "stm32f4xx_rcc.h"
#include "stm32f4xx_crc.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define FWID_SYNC0        0xAAU   /* reply frame start, byte 0                      */
#define FWID_SYNC1        0xBBU   /* reply frame start, byte 1                      */
#define FWID_HDR_LEN      6U      /* sync, sync, frame id, 0, payload length, 0     */
#define FWID_PAYLOAD_LEN  24U     /* six u32 fields, little-endian                  */
#define FWID_REPLY_LEN    31U     /* FWID_HDR_LEN + FWID_PAYLOAD_LEN + XOR checksum */

/* ------------------------------------------------------------------
 * External symbols (Keil linker)
 * ------------------------------------------------------------------ */


/* Start and end of the flash load region LR_IROM1 */
extern uint32_t Load$$LR$$LR_IROM1$$Limit;
extern uint32_t Load$$LR$$LR_IROM1$$Base;

/* ------------------------------------------------------------------
 * Public state
 * ------------------------------------------------------------------ */

volatile fw_identity_t g_fw_identity = {
    0U, 0U, 0U, 0U, 0U, 0U
};

/* ------------------------------------------------------------------
 * Private helpers
 * ------------------------------------------------------------------ */

/* Append value little-endian at out[*idx] and advance *idx by 4. */
static void PutU32Le(uint8_t* out, uint16_t* idx, uint32_t value)
{
    out[(*idx)++] = (uint8_t)(value & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 8) & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 16) & 0xFFU);
    out[(*idx)++] = (uint8_t)((value >> 24) & 0xFFU);
}

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* CRC the load region (falls back to FW_IDENTITY_IMAGE_BASE if the linker base reads 0) and fill g_fw_identity. */
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

/* Write the identity reply into out; returns 1 and sets *out_len, or 0 if out_cap < FWID_REPLY_LEN. */
uint8_t FwIdentity_BuildReply(uint8_t* out, uint16_t out_cap, uint16_t* out_len)
{
    uint16_t idx;
    uint8_t crc;
    uint16_t i;

    if ((out == 0) || (out_len == 0) || (out_cap < FWID_REPLY_LEN)) {
        return 0U;
    }

    out[0] = FWID_SYNC0;
    out[1] = FWID_SYNC1;
    out[2] = FW_IDENTITY_FRAME;
    out[3] = 0x00U;
    out[4] = FWID_PAYLOAD_LEN;
    out[5] = 0x00U;
    idx = FWID_HDR_LEN;

    PutU32Le(out, &idx, g_fw_identity.magic);
    PutU32Le(out, &idx, g_fw_identity.version);
    PutU32Le(out, &idx, g_fw_identity.image_base);
    PutU32Le(out, &idx, g_fw_identity.image_len);
    PutU32Le(out, &idx, g_fw_identity.image_crc32);
    PutU32Le(out, &idx, g_fw_identity.status);

    crc = 0U;   /* XOR of every byte after the two sync bytes */
    for (i = 2U; i < idx; i++) {
        crc ^= out[i];
    }
    out[idx++] = crc;

    *out_len = idx;
    return 1U;
}
