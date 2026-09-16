/**
 * @module  subscribe.c
 * @subsystem  comm
 * @depends  subscribe.h
 * @owns  UART5 address-subscription request parser and reply builder
 * @caution  byte layout is the firmware side of the wire contract pinned in
 *           .agent_contracts/uart5_livewatch_path/spec.md Context and mirrored
 *           in transport.py:_build_request / _decode_tuples
 */

#include "subscribe.h"
#include "FreeRTOS.h"
#include "task.h"     /* xTaskGetTickCount -- the data frames' source clock */
#include "usart5.h"   /* Uart5_Subscribe_TxSend */
#include "usart3.h"   /* Usart3_Stream_TxSend / Usart3_Stream_Busy, USART3_BAUD */

/* File-scope reply buffer. Send_Task stack is 500 words (2 kB); the worst-
 * case reply is 6 (header) + 32 * (6 + 4) (tuples with 4-byte value) + 1 (CRC)
 * = 327 B. tx_buf[512] fits comfortably. */
static uint8_t tx_buf[512];
static uint16_t tx_len;

/* File-scope error-reply buffer. Worst case: 240-byte string + 7 header/CRC
 * = 247 B. err_buf[256] fits comfortably. */
static uint8_t err_buf[256];
static uint16_t err_len;

/* Pending request stashed by Subscribe_ParseRequest; consumed by
 * Subscribe_BuildReply in the same Send_Task cycle. File-scope because
 * Subscribe_Request_t is 1 + 32 * 6 = 193 B; Send_Task stack is 500 words / 2 kB. */
static Subscribe_Request_t s_pending;

/* File-scope scratch buffer for Subscribe_BuildError's CAP-N truncate. The
 * 240-byte error payload cap (per spec) plus a NUL slot is 241 B; putting
 * this on Send_Task's 500-word stack would risk overflow during a 32-tuple
 * rejection (worst-case stack frame: 193 B req + 241 B truncated_msg + 32 B
 * err + housekeeping). */
static uint8_t err_truncate_tmp[241];

/* Default telemetry mode at boot (telemetry-throughput-2026-09-08). Lives
 * here rather than in global_declare.h because the setter/getter pair below
 * must reach it -- and splitting the variable from its API across files would
 * invite the caller to forget the volatile qualifier. Default is MIXED so
 * the dashboard keeps receiving legacy Frame A/B/C the first time the new
 * firmware boots; the host can flip to SUBSCRIBE_ONLY via ground command. */
volatile uint8_t g_telemetry_mode = SUBSCRIBE_DEFAULT_TELEMETRY_MODE;

void SetTelemetryMode(uint8_t mode)
{
    g_telemetry_mode = mode;
}

uint8_t GetTelemetryMode(void)
{
    return g_telemetry_mode;
}

static uint8_t XorCrc(const uint8_t* data, uint16_t len)
{
    uint8_t crc = 0U;
    uint16_t i;
    for (i = 0U; i < len; i++) {
        crc ^= data[i];
    }
    return crc;
}

uint32_t Subscribe_TimeMs(void)
{
    /* configTICK_RATE_HZ is 1000 (FreeRTOSConfig.h:98), so the tick counter IS
     * milliseconds and needs no scaling. Wraps at 2^32 ms, ~49.7 days. */
    return (uint32_t)xTaskGetTickCount();
}

/* CRC16-CCITT (XModem): poly 0x1021, init 0x0000, no reflection, no final XOR.
 * Same parameters Frame C already uses, so the host has one implementation to
 * maintain. Bitwise rather than table-driven: 8 iterations per byte on a 168
 * MHz M4 costs ~1 us for a 30-byte frame, against 512 B of flash for a table. */
static uint16_t Crc16Ccitt(const uint8_t* data, uint16_t len)
{
    uint16_t crc = 0U;
    uint16_t i;
    uint8_t  b;
    for (i = 0U; i < len; i++) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (b = 0U; b < 8U; b++) {
            crc = (uint16_t)((crc & 0x8000U) ? (uint16_t)((crc << 1) ^ 0x1021U)
                                             : (uint16_t)(crc << 1));
        }
    }
    return crc;
}

/* Copy a NUL-terminated ASCII/UTF-8 string into `dst` (up to dst_cap
 * bytes). NUL terminator is copied if dst_cap > 0. No formatting. */
static uint16_t CopyStr(uint8_t* dst, const char* src, uint16_t dst_cap)
{
    uint16_t i;
    if (dst_cap == 0U) {
        return 0U;
    }
    for (i = 0U; i < dst_cap - 1U; i++) {
        if (src[i] == '\0') {
            break;
        }
        dst[i] = (uint8_t)src[i];
    }
    dst[i] = 0U;
    return (uint16_t)(i + 1U);
}

uint8_t Subscribe_ValidateTuple(uint32_t address, uint16_t size,
                                char* err_out, uint16_t err_cap)
{
    uint8_t in_sram;
    uint8_t in_ccm;

    /* Size must be in {1, 2, 4}. */
    if ((size != 1U) && (size != 2U) && (size != 4U)) {
        (void)CopyStr((uint8_t*)err_out, "E:size not in {1,2,4}", err_cap);
        return 0U;
    }
    /* Address must be aligned to size. STM32 unaligned reads can HardFault. */
    if ((address % (uint32_t)size) != 0U) {
        (void)CopyStr((uint8_t*)err_out, "E:addr not aligned to size", err_cap);
        return 0U;
    }
    in_sram = (address >= SUBSCRIBE_ADDR_SRAM_LO) &&
              (address <= SUBSCRIBE_ADDR_SRAM_HI);
    in_ccm  = (address >= SUBSCRIBE_ADDR_CCM_LO) &&
              (address <= SUBSCRIBE_ADDR_CCM_HI);
    if (!in_sram && !in_ccm) {
        /* Diagnostic: include the offending address in hex. Keep the
         * message short — error payload is capped at 240 B. */
        (void)CopyStr((uint8_t*)err_out, "E:addr outside SRAM/CCM", err_cap);
        return 0U;
    }
    /* Read+size upper-bound: address + size must not wrap or exceed the
     * upper end of the matched region. The simple form is "address+size-1
     * in range"; the (size-1) check avoids the wrap corner case at the
     * top of the address space. */
    if ((size > 0U) && (address > (UINT32_MAX - (uint32_t)size))) {
        (void)CopyStr((uint8_t*)err_out, "E:addr+size wrap", err_cap);
        return 0U;
    }
    if (in_sram &&
        ((address + (uint32_t)size - 1U) > SUBSCRIBE_ADDR_SRAM_HI)) {
        (void)CopyStr((uint8_t*)err_out, "E:tuple crosses SRAM end", err_cap);
        return 0U;
    }
    if (in_ccm &&
        ((address + (uint32_t)size - 1U) > SUBSCRIBE_ADDR_CCM_HI)) {
        (void)CopyStr((uint8_t*)err_out, "E:tuple crosses CCM end", err_cap);
        return 0U;
    }
    if (err_out != 0) {
        err_out[0] = '\0';
    }
    return 1U;
}

uint8_t Subscribe_ParseRequest(const uint8_t* buf, uint16_t len,
                               Subscribe_Request_t* req,
                               char* err_out, uint16_t err_cap)
{
    uint8_t i;
    uint16_t payload_len;
    uint8_t count;
    uint16_t expected_frame_len;
    uint8_t calc_crc;
    uint16_t base;
    uint32_t address;
    uint16_t size;

    if (req == 0) {
        (void)CopyStr((uint8_t*)err_out, "E:null req", err_cap);
        return 0U;
    }
    req->count = 0U;
    /* Minimum frame: 6 header + 0 payload + 1 CRC = 7 bytes. */
    if (len < 7U) {
        (void)CopyStr((uint8_t*)err_out, "E:truncated frame", err_cap);
        return 0U;
    }
    if (buf[0] != 0xCCU || buf[1] != 0xDEU) {
        (void)CopyStr((uint8_t*)err_out, "E:bad sync", err_cap);
        return 0U;
    }
    if (buf[2] != SUBSCRIBE_CMD) {
        (void)CopyStr((uint8_t*)err_out, "E:bad cmd", err_cap);
        return 0U;
    }
    payload_len = (uint16_t)(((uint16_t)buf[3] << 8) | (uint16_t)buf[4]);
    if ((payload_len % 6U) != 0U) {
        (void)CopyStr((uint8_t*)err_out, "E:bad len", err_cap);
        return 0U;
    }
    count = buf[5];
    if (count > SUBSCRIBE_MAX_TUPLES) {
        (void)CopyStr((uint8_t*)err_out, "E:too many tuples", err_cap);
        return 0U;
    }
    if ((uint16_t)count * 6U != payload_len) {
        (void)CopyStr((uint8_t*)err_out, "E:len vs count mismatch", err_cap);
        return 0U;
    }
    expected_frame_len = (uint16_t)(6U + payload_len + 1U);
    if (len < expected_frame_len) {
        (void)CopyStr((uint8_t*)err_out, "E:truncated frame", err_cap);
        return 0U;
    }
    calc_crc = XorCrc(&buf[2], (uint16_t)(expected_frame_len - 3U));
    if (calc_crc != buf[expected_frame_len - 1U]) {
        (void)CopyStr((uint8_t*)err_out, "E:CRC mismatch", err_cap);
        return 0U;
    }
    /* Walk the tuple payload. */
    for (i = 0U; i < count; i++) {
        base = (uint16_t)(6U + (uint16_t)i * 6U);
        /* Little-endian un-pack without relying on memcpy alignment:
         * STM32 unaligned 32-bit reads can fault on some compilers. */
        address = ((uint32_t)buf[base]) |
                  ((uint32_t)buf[base + 1U] << 8) |
                  ((uint32_t)buf[base + 2U] << 16) |
                  ((uint32_t)buf[base + 3U] << 24);
        size     = ((uint16_t)buf[base + 4U]) |
                   ((uint16_t)buf[base + 5U] << 8);
        if (Subscribe_ValidateTuple(address, size, err_out, err_cap) == 0U) {
            return 0U;
        }
        req->tuples[i].address = address;
        req->tuples[i].size    = size;
    }
    req->count = count;
    if (err_out != 0) {
        err_out[0] = '\0';
    }
    return 1U;
}

uint8_t Subscribe_BuildReply(const Subscribe_Request_t* req,
                             uint8_t* out, uint16_t out_cap,
                             uint16_t* out_len)
{
    uint8_t i;
    uint16_t idx;
    uint16_t payload_len;
    uint8_t count = req->count;
    /* Compute payload length first to bounds-check the output. */
    payload_len = 0U;
    for (i = 0U; i < count; i++) {
        payload_len = (uint16_t)(payload_len + 6U + req->tuples[i].size);
    }
    if ((uint16_t)(6U + payload_len + 1U) > out_cap) {
        return 0U;
    }
    /* Header. */
    out[0] = 0xAAU;
    out[1] = 0xBBU;
    out[2] = SUBSCRIBE_FRAME_TYPE_REPLY;
    out[3] = (uint8_t)((payload_len >> 8) & 0xFFU);
    out[4] = (uint8_t)(payload_len & 0xFFU);
    out[5] = count;
    idx = 6U;
    /* Tuples: address LE, size LE, value bytes (size many). */
    for (i = 0U; i < count; i++) {
        uint32_t a = req->tuples[i].address;
        uint16_t s = req->tuples[i].size;
        uint8_t k;
        out[idx]     = (uint8_t)(a & 0xFFU);
        out[idx + 1U] = (uint8_t)((a >> 8) & 0xFFU);
        out[idx + 2U] = (uint8_t)((a >> 16) & 0xFFU);
        out[idx + 3U] = (uint8_t)((a >> 24) & 0xFFU);
        out[idx + 4U] = (uint8_t)(s & 0xFFU);
        out[idx + 5U] = (uint8_t)((s >> 8) & 0xFFU);
        idx = (uint16_t)(idx + 6U);
        /* Read `s` bytes from address `a`. Byte-by-byte read keeps us
         * unaligned-safe. Compiler ARMCC V5.06 may synthesise a multi-
         * byte load from an aligned address, which is fine — but a
         * misaligned read on a hardfaulting peripheral map is what we
         * want to avoid, and the validator above already rejected any
         * unaligned (address, size) pair. */
        for (k = 0U; k < s; k++) {
            const uint8_t* p = (const uint8_t*)(a + (uint32_t)k);
            out[idx] = *p;
            idx = (uint16_t)(idx + 1U);
        }
    }
    /* CRC8 XOR over [frame_type, LEN_HI, LEN_LO, MAX_NUM_BASIS, payload...]. */
    out[idx] = XorCrc(&out[2], (uint16_t)(idx - 2U));
    idx = (uint16_t)(idx + 1U);
    *out_len = idx;
    return 1U;
}

uint8_t Subscribe_BuildError(const char* msg, uint8_t count,
                             uint8_t* out, uint16_t out_cap,
                             uint16_t* out_len)
{
    uint16_t idx;
    uint16_t payload_len;
    uint16_t str_len;
    uint16_t truncated_cap = sizeof(err_truncate_tmp);
    /* Cap string at 240 bytes per spec. */
    str_len = CopyStr(err_truncate_tmp, msg, truncated_cap);
    if (str_len > 240U) {
        str_len = 240U;
        err_truncate_tmp[239U] = 0U;
    }
    payload_len = str_len;
    if ((uint16_t)(6U + payload_len + 1U) > out_cap) {
        return 0U;
    }
    out[0] = 0xAAU;
    out[1] = 0xBBU;
    out[2] = SUBSCRIBE_FRAME_TYPE_ERROR;
    out[3] = (uint8_t)((payload_len >> 8) & 0xFFU);
    out[4] = (uint8_t)(payload_len & 0xFFU);
    out[5] = count;
    idx = 6U;
    {
        uint16_t k;
        for (k = 0U; k < str_len; k++) {
            out[idx] = err_truncate_tmp[k];
            idx = (uint16_t)(idx + 1U);
        }
    }
    out[idx] = XorCrc(&out[2], (uint16_t)(idx - 2U));
    idx = (uint16_t)(idx + 1U);
    *out_len = idx;
    return 1U;
}

/* ====================================================================== *
 *  STREAM REPLY ROUTING
 * ======================================================================
 * The 0xCC 0xDE parser (BSP/usart5.c) sets Subscribe_RxTransport to the
 * transport the request arrived on: SUBSCRIBE_RX_TRANSPORT_UART5 (the wired
 * CMSIS-DAP debugger link) or SUBSCRIBE_RX_TRANSPORT_USART3 (the WiFi radio).
 * Replying on a different transport would silently drop 0x08 / 0x7F frames
 * to the operator (CMISIS-DAP dongle not connected, or radio module
 * disconnected). One helper, one routing branch -- keeps every reply site in
 * sync with the parser's transport choice.
 *
 * REPLACES 5 hardcoded Uart5_Subscribe_TxSend() calls (2026-09-08) which
 * always fired the reply over UART5 even when the request arrived on
 * USART3/WiFi. Symptom: direct_subscribe_test.py timed out waiting for the
 * 0x08 schema reply, then the error-log blamed "handshake failed" when the
 * parser had ACKed the request -- the reply was simply going out the wrong
 * UART. The lesson is in `distill-20260823-fix-wifi-reply-routing`.
 */
static void Subscribe_TxReplyToTransport(const uint8_t* buf, uint16_t len)
{
    if ((buf == 0) || (len == 0U)) {
        return;
    }
    if (Subscribe_RxTransport == SUBSCRIBE_RX_TRANSPORT_USART3) {
        (void)Usart3_Stream_TxSend(buf, len);
    } else {
        Uart5_Subscribe_TxSend(buf, len);
    }
}

/* ====================================================================== *
 *  STREAMING SUBSCRIPTION (CMD 0x21)                                     *
 * ====================================================================== */

/* The live subscriptions, one per slot. Written only on an accepted 0x21
 * request; read every Send_Task cycle by Subscribe_StreamTick. */
static Subscribe_Stream_t s_streams[SUBSCRIBE_MAX_SLOTS];

/* Round-robin cursor over the slots. Only ever advances, so a slot that is
 * repeatedly due cannot monopolise the wire and starve the others. */
static uint8_t s_rr;

/* Parse target. A malformed request must NOT disturb a stream that is
 * already running, so parsing lands here and is copied into its slot only
 * after every range has validated. */
static Subscribe_Stream_t s_stream_staging;

/* Data-frame buffer. Held by DMA between arming and completion, hence
 * file-scope: 6 header + 1024 payload + 1 CRC = 1031 B, far past what a
 * 500-word Send_Task stack could carry. */
static uint8_t stream_buf[SUBSCRIBE_STREAM_MAX_BYTES + 8U];
static uint16_t stream_len;

uint8_t Subscribe_ValidateRange(uint32_t address, uint16_t size, uint16_t count,
                                char* err_out, uint16_t err_cap)
{
    uint32_t span;
    uint32_t last;
    uint8_t  in_sram;
    uint8_t  in_ccm;

    if (count == 0U) {
        (void)CopyStr((uint8_t*)err_out, "E:range count is zero", err_cap);
        return 0U;
    }
    /* Element-level checks (size in {1,2,4}, alignment, region membership)
     * reuse the 0x20 validator so the two paths cannot drift apart. */
    if (Subscribe_ValidateTuple(address, size, err_out, err_cap) == 0U) {
        return 0U;
    }
    /* Whole-span check. size <= 4 and count <= 65535 so span <= 262140, and
     * ValidateTuple already proved address <= SUBSCRIBE_ADDR_SRAM_HI, so
     * address + span cannot wrap 32 bits. */
    span = (uint32_t)size * (uint32_t)count;
    last = address + span - 1U;
    in_sram = (address >= SUBSCRIBE_ADDR_SRAM_LO) &&
              (address <= SUBSCRIBE_ADDR_SRAM_HI);
    in_ccm  = (address >= SUBSCRIBE_ADDR_CCM_LO) &&
              (address <= SUBSCRIBE_ADDR_CCM_HI);
    if (in_sram && (last > SUBSCRIBE_ADDR_SRAM_HI)) {
        (void)CopyStr((uint8_t*)err_out, "E:range crosses SRAM end", err_cap);
        return 0U;
    }
    if (in_ccm && (last > SUBSCRIBE_ADDR_CCM_HI)) {
        (void)CopyStr((uint8_t*)err_out, "E:range crosses CCM end", err_cap);
        return 0U;
    }
    if (err_out != 0) {
        err_out[0] = '\0';
    }
    return 1U;
}

uint32_t Subscribe_StreamBps(const Subscribe_Stream_t* st)
{
    if ((st == 0) || (st->active == 0U) || (st->divider == 0U)) {
        return 0U;
    }
    return (((uint32_t)SUBSCRIBE_STREAM_FRAME_OVERHEAD + (uint32_t)st->total_bytes) *
            (uint32_t)SUBSCRIBE_SEND_TASK_HZ) / (uint32_t)st->divider;
}

uint8_t Subscribe_ParseStreamRequest(const uint8_t* buf, uint16_t len,
                                     Subscribe_Stream_t* st,
                                     uint32_t other_bps_uart5,
                                     uint32_t other_bps_usart3,
                                     char* err_out, uint16_t err_cap)
{
    uint8_t  i;
    uint16_t payload_len;
    uint8_t  n_ranges;
    uint8_t  divider;
    uint8_t  transport;
    uint8_t  slot;
    uint16_t expected_frame_len;
    uint8_t  calc_crc;
    uint16_t base;
    uint32_t total;
    uint32_t frame_bytes;
    uint32_t bps;
    uint32_t cap;
    uint32_t pct;

    if (st == 0) {
        (void)CopyStr((uint8_t*)err_out, "E:null stream", err_cap);
        return 0U;
    }
    st->active      = 0U;
    st->n_ranges    = 0U;
    st->total_bytes = 0U;
    st->seq         = 0U;
    st->phase       = 0U;
    st->due         = 0U;
    st->divider     = 0U;
    st->slot        = 0U;
    st->transport   = SUBSCRIBE_TRANSPORT_UART5;

    /* Minimum frame: 6 header + 3 config + 0 ranges + 1 CRC = 10 bytes. */
    if (len < 10U) {
        (void)CopyStr((uint8_t*)err_out, "E:truncated frame", err_cap);
        return 0U;
    }
    if (buf[0] != 0xCCU || buf[1] != 0xDEU) {
        (void)CopyStr((uint8_t*)err_out, "E:bad sync", err_cap);
        return 0U;
    }
    if (buf[2] != SUBSCRIBE_STREAM_CMD) {
        (void)CopyStr((uint8_t*)err_out, "E:bad cmd", err_cap);
        return 0U;
    }
    payload_len = (uint16_t)(((uint16_t)buf[3] << 8) | (uint16_t)buf[4]);
    /* payload = 3 config bytes + N * 8 range bytes. */
    if ((payload_len < 3U) || (((payload_len - 3U) % 8U) != 0U)) {
        (void)CopyStr((uint8_t*)err_out, "E:bad len", err_cap);
        return 0U;
    }
    n_ranges = buf[5];
    if (n_ranges > SUBSCRIBE_MAX_STREAM_RANGES) {
        (void)CopyStr((uint8_t*)err_out, "E:too many ranges", err_cap);
        return 0U;
    }
    if ((uint16_t)(3U + (uint16_t)n_ranges * 8U) != payload_len) {
        (void)CopyStr((uint8_t*)err_out, "E:len vs count mismatch", err_cap);
        return 0U;
    }
    expected_frame_len = (uint16_t)(6U + payload_len + 1U);
    if (len < expected_frame_len) {
        (void)CopyStr((uint8_t*)err_out, "E:truncated frame", err_cap);
        return 0U;
    }
    calc_crc = XorCrc(&buf[2], (uint16_t)(expected_frame_len - 3U));
    if (calc_crc != buf[expected_frame_len - 1U]) {
        (void)CopyStr((uint8_t*)err_out, "E:CRC mismatch", err_cap);
        return 0U;
    }

    divider   = buf[6];
    transport = buf[7];
    slot      = buf[8];

    if (slot >= SUBSCRIBE_MAX_SLOTS) {
        (void)CopyStr((uint8_t*)err_out, "E:bad slot", err_cap);
        return 0U;
    }
    st->slot = slot;

    /* DIVIDER == 0 is an explicit stop for this slot: accept it without
     * looking at the ranges, so the host can halt one with a 10-byte frame. */
    if (divider == 0U) {
        if (err_out != 0) {
            err_out[0] = '\0';
        }
        return 1U;
    }
    if ((transport != SUBSCRIBE_TRANSPORT_UART5) &&
        (transport != SUBSCRIBE_TRANSPORT_USART3)) {
        (void)CopyStr((uint8_t*)err_out, "E:bad transport", err_cap);
        return 0U;
    }
    if (n_ranges == 0U) {
        (void)CopyStr((uint8_t*)err_out, "E:no ranges", err_cap);
        return 0U;
    }

    total = 0U;
    for (i = 0U; i < n_ranges; i++) {
        uint32_t address;
        uint16_t size;
        uint16_t count;
        base = (uint16_t)(9U + (uint16_t)i * 8U);
        address = ((uint32_t)buf[base]) |
                  ((uint32_t)buf[base + 1U] << 8) |
                  ((uint32_t)buf[base + 2U] << 16) |
                  ((uint32_t)buf[base + 3U] << 24);
        size    = ((uint16_t)buf[base + 4U]) |
                  ((uint16_t)buf[base + 5U] << 8);
        count   = ((uint16_t)buf[base + 6U]) |
                  ((uint16_t)buf[base + 7U] << 8);
        if (Subscribe_ValidateRange(address, size, count,
                                    err_out, err_cap) == 0U) {
            return 0U;
        }
        total += (uint32_t)size * (uint32_t)count;
        if (total > (uint32_t)SUBSCRIBE_STREAM_MAX_BYTES) {
            (void)CopyStr((uint8_t*)err_out, "E:stream payload too large",
                          err_cap);
            return 0U;
        }
        st->ranges[i].address = address;
        st->ranges[i].size    = size;
        st->ranges[i].count   = count;
    }

    /* Link-budget guard. Without this a host can ask for 256 float32 at
     * 100 Hz on a 115200 link -- the DMA would simply never keep up and the
     * stream would arrive as shredded partial frames, which is a far worse
     * failure than an up-front rejection.
     *
     * The sum matters, not this slot alone: four slots each at 90% of the
     * link would each pass an isolated check and collectively shred the wire.
     * `other_bps_*` carries what the other slots have already committed. */
    /* 6 header + 4 timestamp + values + 2 CRC16 */
    frame_bytes = (uint32_t)SUBSCRIBE_STREAM_FRAME_OVERHEAD + total;
    bps = (frame_bytes * (uint32_t)SUBSCRIBE_SEND_TASK_HZ) / (uint32_t)divider;
    if (transport == SUBSCRIBE_TRANSPORT_USART3) {
        cap = (uint32_t)USART3_BAUD / 10U;          /* 8N1 = 10 bits per byte */
        pct = (uint32_t)SUBSCRIBE_BUDGET_PCT_USART3;
        bps += other_bps_usart3;
    } else {
        cap = (uint32_t)SUBSCRIBE_UART5_BAUD / 10U;
        pct = (uint32_t)SUBSCRIBE_BUDGET_PCT_UART5;
        bps += other_bps_uart5;
    }
    if (bps > ((cap * pct) / 100U)) {
        (void)CopyStr((uint8_t*)err_out, "E:over link budget", err_cap);
        return 0U;
    }

    st->n_ranges    = n_ranges;
    st->divider     = divider;
    st->transport   = transport;
    st->total_bytes = (uint16_t)total;
    st->active      = 1U;
    if (err_out != 0) {
        err_out[0] = '\0';
    }
    return 1U;
}

uint8_t Subscribe_BuildSchema(const Subscribe_Stream_t* st,
                              uint8_t* out, uint16_t out_cap,
                              uint16_t* out_len)
{
    uint8_t  i;
    uint16_t idx;
    uint16_t payload_len = (uint16_t)(5U + (uint16_t)st->n_ranges * 8U);

    if ((uint16_t)(6U + payload_len + 1U) > out_cap) {
        return 0U;
    }
    out[0] = 0xAAU;
    out[1] = 0xBBU;
    out[2] = SUBSCRIBE_FRAME_TYPE_SCHEMA;
    out[3] = (uint8_t)((payload_len >> 8) & 0xFFU);
    out[4] = (uint8_t)(payload_len & 0xFFU);
    out[5] = st->n_ranges;
    out[6] = st->divider;
    out[7] = st->transport;
    out[8] = st->slot;
    out[9]  = (uint8_t)((st->total_bytes >> 8) & 0xFFU);
    out[10] = (uint8_t)(st->total_bytes & 0xFFU);
    idx = 11U;
    for (i = 0U; i < st->n_ranges; i++) {
        uint32_t a = st->ranges[i].address;
        uint16_t s = st->ranges[i].size;
        uint16_t c = st->ranges[i].count;
        out[idx]      = (uint8_t)(a & 0xFFU);
        out[idx + 1U] = (uint8_t)((a >> 8) & 0xFFU);
        out[idx + 2U] = (uint8_t)((a >> 16) & 0xFFU);
        out[idx + 3U] = (uint8_t)((a >> 24) & 0xFFU);
        out[idx + 4U] = (uint8_t)(s & 0xFFU);
        out[idx + 5U] = (uint8_t)((s >> 8) & 0xFFU);
        out[idx + 6U] = (uint8_t)(c & 0xFFU);
        out[idx + 7U] = (uint8_t)((c >> 8) & 0xFFU);
        idx = (uint16_t)(idx + 8U);
    }
    out[idx] = XorCrc(&out[2], (uint16_t)(idx - 2U));
    idx = (uint16_t)(idx + 1U);
    *out_len = idx;
    return 1U;
}

uint8_t Subscribe_BuildStreamFrame(const Subscribe_Stream_t* st,
                                   uint8_t* out, uint16_t out_cap,
                                   uint16_t* out_len)
{
    uint8_t  i;
    uint16_t idx;
    uint16_t crc;
    uint32_t t_ms;
    /* The 4 timestamp bytes are part of the payload, so LEN stays "everything
     * between the header and the CRC" as it is for every other frame type. */
    uint16_t payload_len = (uint16_t)(4U + st->total_bytes);

    if ((uint16_t)(6U + payload_len + 2U) > out_cap) {
        return 0U;
    }
    out[0] = 0xAAU;
    out[1] = 0xBBU;
    /* Frame type carries the slot: 0x09/0x0A/0x0B/0x0C. The host then routes
     * each slot to its own decoder with no extra framing. */
    out[2] = (uint8_t)(SUBSCRIBE_FRAME_TYPE_DATA + st->slot);
    out[3] = (uint8_t)((payload_len >> 8) & 0xFFU);
    out[4] = (uint8_t)(payload_len & 0xFFU);
    out[5] = st->seq;
    /* Sampled here, in the same cycle that copies the values below, so it
     * timestamps the DATA rather than the transmission. */
    t_ms = Subscribe_TimeMs();
    out[6] = (uint8_t)(t_ms & 0xFFU);
    out[7] = (uint8_t)((t_ms >> 8) & 0xFFU);
    out[8] = (uint8_t)((t_ms >> 16) & 0xFFU);
    out[9] = (uint8_t)((t_ms >> 24) & 0xFFU);
    idx = 10U;
    /* Values only -- the schema went out once in the 0x08 frame. Byte-wise
     * reads keep this unaligned-safe; the validator already rejected any
     * misaligned (address, size) pair. */
    for (i = 0U; i < st->n_ranges; i++) {
        uint32_t a      = st->ranges[i].address;
        uint32_t nbytes = (uint32_t)st->ranges[i].size *
                          (uint32_t)st->ranges[i].count;
        uint32_t k;
        for (k = 0U; k < nbytes; k++) {
            const uint8_t* p = (const uint8_t*)(a + k);
            out[idx] = *p;
            idx = (uint16_t)(idx + 1U);
        }
    }
    /* CRC16-CCITT, not the XOR used by the control-plane frames: these frames
     * are the recorded dataset, and XOR cannot see a byte transposition. */
    crc = Crc16Ccitt(&out[2], (uint16_t)(idx - 2U));
    out[idx]      = (uint8_t)((crc >> 8) & 0xFFU);
    out[idx + 1U] = (uint8_t)(crc & 0xFFU);
    idx = (uint16_t)(idx + 2U);
    *out_len = idx;
    return 1U;
}

uint8_t Subscribe_StreamOwnsUsart3(void)
{
    uint8_t i;
    for (i = 0U; i < SUBSCRIBE_MAX_SLOTS; i++) {
        if ((s_streams[i].active != 0U) &&
            (s_streams[i].transport == SUBSCRIBE_TRANSPORT_USART3)) {
            return 1U;
        }
    }
    return 0U;
}

void Subscribe_StreamTick(void)
{
    uint8_t i;
    uint8_t slot;
    Subscribe_Stream_t* st;
    uint8_t frames_sent;
    uint8_t served;

    /* Phase 1: advance every slot's clock and mark those that came due.
     * Marking is separate from sending because only one frame goes out per
     * cycle -- a slot that misses its turn stays due rather than losing the
     * sample silently. */
    for (i = 0U; i < SUBSCRIBE_MAX_SLOTS; i++) {
        if (s_streams[i].active == 0U) {
            continue;
        }
        s_streams[i].phase++;
        if (s_streams[i].phase >= s_streams[i].divider) {
            s_streams[i].phase = 0U;
            s_streams[i].due   = 1U;
        }
    }

    /* Phase 2: serve up to SUBSCRIBE_MAX_FRAMES_PER_TICK due slots, resuming
     * from where the last tick left off (s_rr). Round-robin within the tick
     * means a fast slot cannot permanently starve a slower one behind it --
     * after each service s_rr advances, so the next inner sweep picks up at
     * the slot AFTER the one we just served. Backpressured slots still drop
     * their sample (counted gap) and move on; the wire-budget guard keeps
     * the aggregate per transport inside the link's 95% cap, so N is a
     * per-tick SERVICE quota, not a wire-cap multiplier. */
    frames_sent = 0U;
    while (frames_sent < SUBSCRIBE_MAX_FRAMES_PER_TICK) {
        served = 0U;
        for (i = 0U; i < SUBSCRIBE_MAX_SLOTS; i++) {
            slot = (uint8_t)((s_rr + i) % SUBSCRIBE_MAX_SLOTS);
            st   = &s_streams[slot];
            if ((st->active == 0U) || (st->due == 0U)) {
                continue;
            }

            /* Ask the transport BEFORE building. Usart3_Stream_Busy() means
             * BACKPRESSURE since the 2026-08-09 TX ring rework: the ring is over
             * half full, i.e. the wire is not draining as fast as Send_Task is
             * offering. (Usart3_Stream_TxSend copies synchronously, so stream_buf
             * is no longer held under DMA -- the old corruption worry is gone.)
             * The right move under sustained backpressure is to drop THIS sample,
             * not to let the slot stay due and burst two frames next cycle deeper
             * into backlog: bumping seq leaves the gap the host counts as loss,
             * and a dataset with counted gaps beats one with smeared cadence. */
            if (st->transport == SUBSCRIBE_TRANSPORT_USART3) {
                if (Usart3_Stream_Busy() != 0U) {
                    st->due = 0U;
                    st->seq++;
                    s_rr = (uint8_t)((slot + 1U) % SUBSCRIBE_MAX_SLOTS);
                    served = 1U;
                    frames_sent++;
                    break;
                }
            }
            if (Subscribe_BuildStreamFrame(st, stream_buf,
                                           (uint16_t)sizeof(stream_buf),
                                           &stream_len) != 1U) {
                st->due = 0U;
                s_rr = (uint8_t)((slot + 1U) % SUBSCRIBE_MAX_SLOTS);
                served = 1U;
                frames_sent++;
                break;
            }
            if (st->transport == SUBSCRIBE_TRANSPORT_USART3) {
                (void)Usart3_Stream_TxSend(stream_buf, stream_len);
            } else {
                Uart5_Subscribe_TxSend(stream_buf, stream_len);
            }
            st->due = 0U;
            st->seq++;
            s_rr = (uint8_t)((slot + 1U) % SUBSCRIBE_MAX_SLOTS);
            served = 1U;
            frames_sent++;
            break;
        }
        /* Inner sweep ran the full round with nothing served -> no more due
         * slots in this tick. s_rr stays where the last successful (or
         * empty) sweep left it, so the next tick picks up there. */
        if (served == 0U) {
            break;
        }
    }
}

/* Handle an accepted 0x21 request: commit it and acknowledge with the
 * schema frame. Split out of Uart5_Subscribe_HandleRequest to keep that
 * function's stack frame flat. */
static void HandleStreamRequest(char* err, uint16_t err_cap)
{
    uint8_t  i;
    uint8_t  target;
    uint32_t other5 = 0U;
    uint32_t other3 = 0U;

    /* The slot this request targets is byte 8, but the frame has not been
     * validated yet -- so sum the budget of every slot EXCEPT that one, and
     * fall back to summing all of them if the byte is out of range (the
     * parser will reject it anyway, and an over-estimate only rejects
     * earlier). */
    target = (UA5RxSubscribeLen >= 9U) ? UA5RxSubscribeBuf[8] : 0xFFU;
    for (i = 0U; i < SUBSCRIBE_MAX_SLOTS; i++) {
        if (i == target) {
            continue;               /* this slot is being replaced */
        }
        if (s_streams[i].transport == SUBSCRIBE_TRANSPORT_USART3) {
            other3 += Subscribe_StreamBps(&s_streams[i]);
        } else {
            other5 += Subscribe_StreamBps(&s_streams[i]);
        }
    }

    if (Subscribe_ParseStreamRequest(UA5RxSubscribeBuf, UA5RxSubscribeLen,
                                     &s_stream_staging, other5, other3,
                                     err, err_cap) != 1U) {
        uint8_t count = 0U;
        if (UA5RxSubscribeLen >= 6U) {
            count = UA5RxSubscribeBuf[5];
        }
        if (Subscribe_BuildError(err, count, err_buf,
                                 (uint16_t)sizeof(err_buf), &err_len) == 1U) {
            Subscribe_TxReplyToTransport(err_buf, err_len);
        }
        return;
    }
    /* Commit only now -- a rejected request must leave a running stream
     * untouched. The parser has bounds-checked s_stream_staging.slot. */
    s_streams[s_stream_staging.slot] = s_stream_staging;
    if (Subscribe_BuildSchema(&s_stream_staging, tx_buf,
                              (uint16_t)sizeof(tx_buf), &tx_len) != 1U) {
        (void)Subscribe_BuildError("E:schema too large",
                                   s_stream_staging.n_ranges,
                                   err_buf, (uint16_t)sizeof(err_buf),
                                   &err_len);
        Subscribe_TxReplyToTransport(err_buf, err_len);
        return;
    }
    Subscribe_TxReplyToTransport(tx_buf, tx_len);
}

/* Build a reply from the staged IRQ-side frame and hand it off to the
 * DMA. Called from TASK/send_data.c (Send_Task context) AFTER the live
 * telemetry DMA has completed. The reply is fired on a *second* DMA
 * turn so the existing A/B stream is unchanged. */
void Uart5_Subscribe_HandleRequest(void)
{
    Subscribe_Request_t* req = &s_pending;
    char err[32];

    if (UA5RxSubscribePending == 0U) {
        return;
    }
    /* Dispatch on the CMD byte: 0x21 = streaming subscribe, 0x20 = the
     * original one-shot read. The IRQ-side parser has already CRC-checked
     * whichever landed. */
    if ((UA5RxSubscribeLen >= 3U) &&
        (UA5RxSubscribeBuf[2] == SUBSCRIBE_STREAM_CMD)) {
        HandleStreamRequest(err, (uint16_t)sizeof(err));
        UA5RxSubscribePending = 0U;
        return;
    }
    if (Subscribe_ParseRequest(UA5RxSubscribeBuf, UA5RxSubscribeLen,
                               req, err, sizeof(err)) != 1U) {
        /* Validation failed -> build a 0x7F error reply echoing the
         * tuple count we observed (or 0 if we never got that far). */
        uint8_t count = 0U;
        if (UA5RxSubscribeLen >= 6U) {
            count = UA5RxSubscribeBuf[5];
        }
        if (Subscribe_BuildError(err, count, err_buf,
                                 (uint16_t)sizeof(err_buf),
                                 &err_len) != 1U) {
            /* Should not happen with err_buf[256] and a 31-byte err[]. */
            UA5RxSubscribePending = 0U;
            return;
        }
        Subscribe_TxReplyToTransport(err_buf, err_len);
    } else {
        if (Subscribe_BuildReply(req, tx_buf, (uint16_t)sizeof(tx_buf),
                                 &tx_len) != 1U) {
            (void)Subscribe_BuildError("E:reply too large", req->count,
                                       err_buf, (uint16_t)sizeof(err_buf),
                                       &err_len);
            Subscribe_TxReplyToTransport(err_buf, err_len);
        } else {
            Subscribe_TxReplyToTransport(tx_buf, tx_len);
        }
    }
    UA5RxSubscribePending = 0U;
}