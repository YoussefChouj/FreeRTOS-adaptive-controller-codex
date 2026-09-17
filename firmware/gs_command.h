#ifndef PLATFORM_GS_COMMAND_H
#define PLATFORM_GS_COMMAND_H

#include <stdint.h>

/* Shared queue record for the legacy command path and S3 transactions. The
 * first three fields retain the legacy command meaning; metadata is zero for
 * legacy frames and nonzero for a versioned transaction. */
typedef struct {
    uint8_t id;
    uint8_t index;
    float value;
    uint16_t transaction_id;
    uint8_t transaction_flags;
    uint8_t transaction_transport;
} GS_Cmd_t;

extern volatile GS_Cmd_t gs_cmd_queue[16];
extern volatile uint8_t gs_cmd_head;
extern volatile uint8_t gs_cmd_tail;

#endif
