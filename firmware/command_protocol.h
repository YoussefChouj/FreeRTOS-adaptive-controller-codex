#ifndef PLATFORM_COMMAND_PROTOCOL_H
#define PLATFORM_COMMAND_PROTOCOL_H

#include <stdint.h>

#define PLATFORM_COMMAND_SYNC_HI 0xCCU
#define PLATFORM_COMMAND_SYNC_LO 0xDFU
#define PLATFORM_COMMAND_VERSION 1U
#define PLATFORM_COMMAND_MAX_PAYLOAD 64U

typedef enum {
    PLATFORM_COMMAND_OK = 0,
    PLATFORM_COMMAND_BAD_FRAME = 1,
    PLATFORM_COMMAND_BAD_VERSION = 2,
    PLATFORM_COMMAND_BAD_LENGTH = 3,
    PLATFORM_COMMAND_BAD_CRC = 4
} PlatformCommandStatus_e;

#define PLATFORM_RESULT_ACK       0U
#define PLATFORM_RESULT_REJECTED  1U
#define PLATFORM_RESULT_APPLIED   2U

typedef struct {
    uint16_t transaction_id;
    uint8_t version;
    uint8_t flags;
    uint8_t command_id;
    uint8_t index;
    uint16_t payload_len;
    const uint8_t* payload;
} PlatformCommand_t;

/* Parse one complete CC DF transaction frame. The returned payload points
 * into buf and remains valid for the caller's frame lifetime. */
PlatformCommandStatus_e PlatformCommand_Parse(const uint8_t* buf,
                                              uint16_t len,
                                              PlatformCommand_t* out);

uint8_t PlatformCommand_Xor(const uint8_t* data, uint16_t len);

/* Build an AA BB 0x30..0x32 result frame. `detail` may be null and is
 * truncated to keep the control-plane frame bounded. */
uint8_t PlatformCommand_BuildResult(uint16_t transaction_id,
                                     uint8_t outcome,
                                     uint8_t command_id,
                                     uint8_t index,
                                     uint8_t reason,
                                     const char* detail,
                                     uint8_t* out,
                                     uint16_t out_cap,
                                     uint16_t* out_len);

#endif
