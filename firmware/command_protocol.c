#include "command_protocol.h"
#include <stddef.h>

uint8_t PlatformCommand_Xor(const uint8_t* data, uint16_t len)
{
    uint8_t value = 0U;
    uint16_t i;
    for (i = 0U; i < len; i++) {
        value ^= data[i];
    }
    return value;
}

uint8_t PlatformCommand_BuildResult(uint16_t transaction_id,
                                     uint8_t outcome,
                                     uint8_t command_id,
                                     uint8_t index,
                                     uint8_t reason,
                                     const char* detail,
                                     uint8_t* out,
                                     uint16_t out_cap,
                                     uint16_t* out_len)
{
    uint8_t detail_len = 0U;
    uint16_t i;
    uint16_t body_len;
    uint16_t idx = 0U;

    if ((out == 0) || (out_len == 0) || (out_cap < 15U) ||
        (outcome > PLATFORM_RESULT_APPLIED)) {
        return 0U;
    }
    if (detail != 0) {
        while ((detail[detail_len] != '\0') && (detail_len < 48U)) {
            detail_len++;
        }
    }
    body_len = (uint16_t)(8U + detail_len);
    if (out_cap < (uint16_t)(6U + body_len)) {
        return 0U;
    }
    out[idx++] = 0xAAU;
    out[idx++] = 0xBBU;
    out[idx++] = (uint8_t)(0x30U + outcome);
    out[idx++] = (uint8_t)((body_len >> 8) & 0xFFU);
    out[idx++] = (uint8_t)(body_len & 0xFFU);
    out[idx++] = PLATFORM_COMMAND_VERSION;
    out[idx++] = outcome;
    out[idx++] = (uint8_t)(transaction_id & 0xFFU);
    out[idx++] = (uint8_t)((transaction_id >> 8) & 0xFFU);
    out[idx++] = command_id;
    out[idx++] = index;
    out[idx++] = reason;
    out[idx++] = detail_len;
    for (i = 0U; i < detail_len; i++) {
        out[idx++] = (uint8_t)detail[i];
    }
    out[idx] = PlatformCommand_Xor(&out[2], (uint16_t)(3U + body_len));
    idx++;
    *out_len = idx;
    return 1U;
}

PlatformCommandStatus_e PlatformCommand_Parse(const uint8_t* buf,
                                              uint16_t len,
                                              PlatformCommand_t* out)
{
    uint16_t payload_len;
    uint16_t body_len;

    if ((buf == 0) || (out == 0) || (len < 15U)) {
        return PLATFORM_COMMAND_BAD_FRAME;
    }
    if ((buf[0] != PLATFORM_COMMAND_SYNC_HI) ||
        (buf[1] != PLATFORM_COMMAND_SYNC_LO)) {
        return PLATFORM_COMMAND_BAD_FRAME;
    }
    body_len = (uint16_t)(len - 3U);
    if (PlatformCommand_Xor(&buf[2], body_len) != buf[len - 1U]) {
        return PLATFORM_COMMAND_BAD_CRC;
    }
    if (buf[2] != PLATFORM_COMMAND_VERSION) {
        return PLATFORM_COMMAND_BAD_VERSION;
    }
    payload_len = (uint16_t)buf[8] | ((uint16_t)buf[9] << 8);
    if ((payload_len == 0U) || (payload_len > PLATFORM_COMMAND_MAX_PAYLOAD) ||
        ((uint16_t)(11U + payload_len) != len)) {
        return PLATFORM_COMMAND_BAD_LENGTH;
    }
    out->version = buf[2];
    out->flags = buf[3];
    out->transaction_id = (uint16_t)buf[4] | ((uint16_t)buf[5] << 8);
    out->command_id = buf[6];
    out->index = buf[7];
    out->payload_len = payload_len;
    out->payload = &buf[10];
    return PLATFORM_COMMAND_OK;
}
