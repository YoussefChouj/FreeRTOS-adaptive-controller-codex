#ifndef  __SUBSCRIBE_H__
#define  __SUBSCRIBE_H__

/* UART5 extended-prefix (0xCC 0xDE) subscription producer.
 *
 * Implements the firmware-side of HANDOFF §5 item 1: host sends a list of
 * (address:uint32 LE, size:uint16 LE) tuples on UART5; firmware validates
 * each tuple against the SRAM/CCM allowlist, packs the requested bytes
 * from those addresses, and replies with either a 0x07 tuple reply or a
 * 0x7F error reply. Read-only observation: nothing in this module writes
 * to the requested addresses.
 *
 * Pinned byte layout (FC -> host reply frame, frame_type 0x07):
 *
 *   offset  width  field                                         value
 *   0       1      SYNC_HI                                       0xAA
 *   1       1      SYNC_LO                                       0xBB
 *   2       1      FRAME_TYPE                                    0x07
 *   3       1      LEN_HI                                        (payload length >> 8) & 0xFF
 *   4       1      LEN_LO                                        (payload length) & 0xFF
 *   5       1      MAX_NUM_BASIS                                 tuple count
 *   6       6*N+sum(size_i)   payload: N tuples of (address:uint32 LE,
 *                          size:uint16 LE, value:size bytes)
 *   trailer CRC8 XOR over [FRAME_TYPE, LEN_HI, LEN_LO, MAX_NUM_BASIS, payload...]
 *
 * Pinned byte layout (FC -> host error-reply frame, frame_type 0x7F):
 *
 *   offset  width  field                                         value
 *   0       1      SYNC_HI                                       0xAA
 *   1       1      SYNC_LO                                       0xBB
 *   2       1      FRAME_TYPE                                    0x7F
 *   3       1      LEN_HI                                        (payload length >> 8) & 0xFF
 *   4       1      LEN_LO                                        (payload length) & 0xFF
 *   5       1      MAX_NUM_BASIS                                 echo of requested tuple count
 *   6       ...    payload: UTF-8 string, NUL-terminated, <= 240 bytes
 *   trailer CRC8 XOR over [FRAME_TYPE, LEN_HI, LEN_LO, MAX_NUM_BASIS, payload...]
 *
 * Constraints:
 *  - Subscriptions are read-only; nothing here writes back to the address.
 *  - max 32 tuples per request (subscribe.h MAX_TUPLES).
 *  - Address allowlist: SRAM 0x20000000..0x2001FFFF and CCM 0x10000000..0x1000FFFF.
 *    Bounds derived from OBJ/JX_FLY.map RW_IRAM1 region (Max 0x20000) and
 *    STM32F407 datasheet (CCM 64 KB at 0x10000000).
 *  - size must be in {1, 2, 4}; address must be aligned to size.
 *  - Send_Task stack is 500 words (2 kB); no stack locals > 32 B. Reply
 *    and error buffers are file-scope statics in API/subscribe.c.
 */

#include "stm32f4xx.h"

#define SUBSCRIBE_MAX_TUPLES       32U
#define SUBSCRIBE_FRAME_TYPE_REPLY  0x07U
#define SUBSCRIBE_FRAME_TYPE_ERROR 0x7FU
#define SUBSCRIBE_CMD               0x20U
/* SRAM bound: STM32F407 has 128 KB SRAM starting at 0x20000000; the
 * linker's RW_IRAM1 region is `Max: 0x00020000` (= 128 KB). Verified
 * against OBJ/JX_FLY.map (RW_IRAM1 Base 0x20000000, Size 0x0001b388,
 * Max 0x00020000). */
/* The #ifndef guards exist so the host test harness can retarget the allowlist
 * at its own memory and exercise the real parser. The FIRMWARE BUILD MUST NEVER
 * DEFINE THESE -- doing so silently widens what a host can ask the drone to
 * read. They are absent from JX_FLY.uvprojx and must stay that way. */
#ifndef SUBSCRIBE_ADDR_SRAM_LO
#define SUBSCRIBE_ADDR_SRAM_LO      0x20000000U
#endif
#ifndef SUBSCRIBE_ADDR_SRAM_HI
#define SUBSCRIBE_ADDR_SRAM_HI      0x2001FFFFU
#endif
/* CCM bound: STM32F407 has 64 KB CCM at 0x10000000..0x1000FFFF. The
 * current link map places no symbols there, but the physical address
 * range is valid; reading from it returns whatever happens to be
 * physically present (typically zero, not a HardFault). */
#define SUBSCRIBE_ADDR_CCM_LO       0x10000000U
#define SUBSCRIBE_ADDR_CCM_HI       0x1000FFFFU

/* ---- Streaming subscription (CMD 0x21) ---------------------------------
 *
 * The 0x20 path above is a POLL: one request, one reply, done. Streaming
 * adds three things it lacks -- repetition at a chosen rate, a choice of
 * transport, and range tuples.
 *
 * Range tuples are what make it affordable. The 0x07 reply repeats the
 * address and size in every frame: 10 bytes on the wire to deliver one
 * float32, 60% overhead. A stream re-sends that identical schema 80 times
 * a second for no information. So the schema is sent ONCE (frame 0x08) at
 * subscribe time and the data frames (0x09) carry values only:
 *
 *     32 float32, repeating schema : 6 + 32*10 + 1 = 327 B  -> 26.3 kB/s
 *     32 float32, schema once      : 6 + 32*4  + 1 = 135 B  -> 10.9 kB/s
 *
 * and `count` lets one tuple name a whole contiguous array, so a request
 * stays ~200 B whether it subscribes to 8 values or 256.
 *
 * Host -> FC request frame (CMD 0x21, on UART5):
 *
 *   offset  width  field
 *   0       1      SYNC_HI                    0xCC
 *   1       1      SYNC_LO                    0xDE
 *   2       1      CMD                        0x21
 *   3       1      LEN_HI                     (payload length >> 8) & 0xFF
 *   4       1      LEN_LO                     (payload length) & 0xFF
 *   5       1      N_RANGES                   range count
 *   6       1      DIVIDER                    0 = stop; k = emit every k-th cycle
 *   7       1      TRANSPORT                  0 = UART5, 1 = USART3
 *   8       1      SLOT                       0..SUBSCRIBE_MAX_SLOTS-1
 *   9       8*N    payload: N ranges of (address:uint32 LE, size:uint16 LE,
 *                                        count:uint16 LE)
 *   trailer CRC8 XOR over [CMD .. payload]
 *
 *   payload length = 3 + N*8  (the three config bytes count as payload)
 *
 * FC -> host schema frame (0x08, replied on UART5, once per accepted request):
 *
 *   0..2    3      0xAA 0xBB 0x08
 *   3..4    2      LEN_HI LEN_LO              payload length = 5 + N*8
 *   5       1      N_RANGES
 *   6       1      DIVIDER                    as accepted
 *   7       1      TRANSPORT                  as accepted
 *   8       1      SLOT                       as accepted
 *   9..10   2      TOTAL_BYTES (BE)           data-frame payload width
 *   11      8*N    the accepted ranges, echoed
 *   trailer CRC8 XOR
 *
 * FC -> host data frame, on the selected transport, every DIVIDER cycles.
 * The frame type encodes the slot: 0x09 + slot, i.e. 0x09/0x0A/0x0B/0x0C.
 * That way a host decodes each slot independently with no extra framing.
 *
 *   0..2    3      0xAA 0xBB (0x09 + SLOT)
 *   3..4    2      LEN_HI LEN_LO              payload length = 4 + TOTAL_BYTES
 *   5       1      SEQ                        per-slot; gaps = dropped frames
 *   6..9    4      T_MS (uint32 LE)           SOURCE timestamp, ms since boot
 *   10      ...    values, packed in range order, no addresses
 *   trailer CRC16-CCITT (XModem), big-endian, over [FRAME_TYPE .. last value]
 *
 * T_MS is `xTaskGetTickCount()` and configTICK_RATE_HZ is 1000, so it is
 * milliseconds since boot, sampled in the same Send_Task cycle that copies the
 * values. It exists because the host's arrival time is smeared by USB and OS
 * scheduling -- fine for "is it adapting", useless for system identification.
 * Values inside ONE frame were always simultaneous; T_MS is what makes the
 * spacing BETWEEN frames trustworthy. It wraps at 2^32 ms (~49.7 days).
 *
 * CRC16-CCITT rather than the CRC8 XOR used elsewhere: XOR cannot see byte
 * transpositions or any even number of bit flips in a column, so a corrupted
 * frame can pass and land in the dataset looking plausible. These frames ARE
 * the dataset. The 0x08 schema and 0x7F error frames keep CRC8 -- they are
 * one-shot control-plane frames parsed by the shared envelope reader, and a
 * corrupted schema is caught instead by the host checking that every echoed
 * range is one it actually asked for.
 *
 * MULTI-RATE. Slots are independent subscriptions with their own variables,
 * rate and transport, which is how one link carries signals that deserve very
 * different attention:
 *
 *     slot 0  Theta weights + attitude   divider 1   ~80 Hz
 *     slot 1  battery, RPM               divider 8   ~10 Hz
 *     slot 2  EKF states                 divider 40   ~2 Hz
 *
 * At most SUBSCRIBE_MAX_FRAMES_PER_TICK data frames are emitted per Send_Task
 * cycle. When several slots come due together they are served round-robin and
 * the unserved ones stay due, so a busy slot cannot starve a quiet one. The
 * budget guard sums ALL slots on a transport, so slots cannot collectively
 * oversubscribe the link.
 *
 * Rejections reuse the existing 0x7F error frame on UART5.
 *
 * SECURITY NOTE: the request is accepted on UART5 only -- the wireless
 * CMSIS-DAP link. USART3 (the radio) carries the data stream but still has
 * NO command parser, so promoting telemetry to the radio adds no inbound
 * attack surface. Control plane on the debugger, data plane on the radio.
 */
#define SUBSCRIBE_STREAM_CMD         0x21U
#define SUBSCRIBE_FRAME_TYPE_SCHEMA  0x08U
#define SUBSCRIBE_FRAME_TYPE_DATA    0x09U

/* Non-value bytes in a data frame: 6 header + 4 timestamp + 2 CRC16. The
 * budget guard and the host's bandwidth arithmetic must agree on this. */
#define SUBSCRIBE_STREAM_FRAME_OVERHEAD 12U

/* Source clock for the data frames, in milliseconds. Separated from the
 * frame builder so the host test harness can drive it deterministically. */
uint32_t Subscribe_TimeMs(void);

/* 62 ranges, not the historical 24: the host encoder (ground_station/livewatch/
 * stream.py) was already raising to 62 in 2026-09, and the limit-test harness
 * exercises 50+ ranges (scratch/results_final.json). USART3 mailbox grew
 * 96 -> 256 -> 512 B (2026-09-05/07), so 62 * 8 B + 10 B overhead = 506 B fits
 * with 6 B to spare. Range tuples make a higher count pointless -- one range
 * already covers a whole contiguous weight vector. */
#define SUBSCRIBE_MAX_STREAM_RANGES  62U

/* Concurrent subscriptions, each with its own variables, rate and transport.
 * Four because the data frame type is 0x09 + slot and 0x0D is where the next
 * frame-type block would start; also 4 * sizeof(Subscribe_Stream_t) = 816 B of
 * the ~19 kB free SRAM, which is affordable, and 8 would not obviously be. */
#define SUBSCRIBE_MAX_SLOTS          4U

/* Per-Send_Task-cycle emission quota. Before telemetry-throughput-2026-09-08
 * Subscribe_StreamTick emitted AT MOST one frame per tick (round-robin), so
 * N due slots had to wait N ticks before all of them were served -- a multi-
 * slot subscription was effectively divided by its slot count. Bumping the
 * quota to SUBSCRIBE_MAX_SLOTS lets the round-robin catch up inside one tick
 * when the link has headroom. The wire-budget guard (SUBSCRIBE_BUDGET_PCT_*)
 * is unchanged, so this is a per-tick service quota, not a wire-cap
 * multiplier -- four 64 B frames at 200 Hz still fit the 95% USART3 budget. */
#define SUBSCRIBE_MAX_FRAMES_PER_TICK  SUBSCRIBE_MAX_SLOTS

/* Max data-frame payload. 1024 B = 256 float32, which at the nominal 100 Hz
 * over a 921600 link is already past the budget guard below, so a larger
 * buffer would be RAM that can never be filled. */
#define SUBSCRIBE_STREAM_MAX_BYTES   1024U

#define SUBSCRIBE_TRANSPORT_UART5    0U
#define SUBSCRIBE_TRANSPORT_USART3   1U

/* Reply transport follows the accepted request. Keep these values aligned
 * with SUBSCRIBE_TRANSPORT_* because USART3 uses the same physical link. */
#define SUBSCRIBE_RX_TRANSPORT_UART5   SUBSCRIBE_TRANSPORT_UART5
#define SUBSCRIBE_RX_TRANSPORT_USART3  SUBSCRIBE_TRANSPORT_USART3

/* Compile-time switch for the "subscribe-default" build (telemetry-throughput-
 * 2026-09-08). When defined (=1), USER/main.c Send_Task gates the legacy
 * Frame A/B/C emission on g_telemetry_mode: in SUBSCRIBE_ONLY the legacy
 * path is skipped entirely (Send_Task runs at its nominal 200 Hz because
 * the ~3.7 ms UART4 DMA busy-wait goes away); in MIXED (default) both
 * paths coexist. When NOT defined, the call site falls back to the original
 * always-emit-legacy behaviour for binary compatibility with the prior
 * release. */
#ifndef SUBSCRIBE_BUILD_DEFAULT_SUBSCRIBE
#define SUBSCRIBE_BUILD_DEFAULT_SUBSCRIBE  1U
#endif

/* Telemetry mode knob. Toggled at runtime by SetTelemetryMode(); default is
 * SUBSCRIBE_TELEMETRY_MIXED so dashboard continuity survives the upgrade --
 * legacy Frame A/B/C continues to arrive AND the subscribe stream can be
 * opened in parallel on USART3. SUBSCRIBE_TELEMETRY_SUBSCRIBE_ONLY skips
 * the UART4 legacy path entirely, freeing Send_Task from the ~3.7 ms DMA
 * busy-wait that had been pacing it to ~80 Hz. SUBSCRIBE_TELEMETRY_LEGACY
 * is the original always-emit behaviour (no suppression, no subscribe). */
typedef enum {
    SUBSCRIBE_TELEMETRY_LEGACY        = 0U,
    SUBSCRIBE_TELEMETRY_MIXED         = 1U,
    SUBSCRIBE_TELEMETRY_SUBSCRIBE_ONLY = 2U,
} Subscribe_TelemetryMode_e;

/* Default value of g_telemetry_mode at boot (telemetry-throughput-2026-09-08).
 * Compile-time overridable for tests; MIXED is the production default so the
 * dashboard keeps receiving legacy Frame A/B/C alongside any subscribed
 * stream. The benchmark build overrides to SUBSCRIBE_ONLY via the header so
 * Send_Task runs at nominal 200 Hz without the ~3.7 ms UART4 DMA busy-wait. */
#ifndef SUBSCRIBE_DEFAULT_TELEMETRY_MODE
#define SUBSCRIBE_DEFAULT_TELEMETRY_MODE  SUBSCRIBE_TELEMETRY_MIXED
#endif

/* Send_Task's measured cadence is 80.4 Hz in SUBSCRIBE_TELEMETRY_MIXED
 * (the legacy Frame A/B/C UART4 DMA busy-wait steals ~3 ms from the
 * 5 ms task floor, pacing the loop to ~80 Hz). SUBSCRIBE_TELEMETRY_
 * SUBSCRIBE_ONLY skips the DMA path and runs at the nominal 200 Hz.
 * SUBSCRIBE_SEND_TASK_HZ = 80U matches MIXED so the divider formula
 * `int(80 / hz)` is exact and the wire-budget guard is honest; to
 * exceed 80 Hz per slot the host calls
 * SetTelemetryMode(SUBSCRIBE_TELEMETRY_SUBSCRIBE_ONLY) first.
 *
 * Link-budget guard. USART3 owns the link while a stream is active
 * (usart3_send() is suppressed), so its budget is the wire ceiling:
 * 95% of USART3_BAUD/10 = 87552 B/s at the attained 913043 baud.
 * UART5 is not empty: frames A/B/C already measure 8569 B/s = 74% of
 * its 11520 B/s capacity, so its guard sits at 20%. The last 5% of
 * USART3 is deliberately unallocatable, not an oversight. */
#define SUBSCRIBE_SEND_TASK_HZ       80U
#define SUBSCRIBE_BUDGET_PCT_USART3  95U
#define SUBSCRIBE_BUDGET_PCT_UART5   20U
/* Mirrors BSP/usart5.c:55. Only the budget guard reads it, and a stale value
 * here can only make the guard stricter, never looser. */
#define SUBSCRIBE_UART5_BAUD         115200U

typedef struct {
    uint32_t address;
    uint16_t size;
} Subscribe_Tuple_t;

/* One contiguous run of `count` elements of `size` bytes starting at
 * `address`. count == 1 degenerates to the old single-value tuple. */
typedef struct {
    uint32_t address;
    uint16_t size;
    uint16_t count;
} Subscribe_Range_t;

typedef struct {
    uint8_t  active;       /* 0 = no stream running */
    uint8_t  n_ranges;
    uint8_t  divider;      /* emit every divider-th Send_Task cycle */
    uint8_t  transport;    /* SUBSCRIBE_TRANSPORT_* */
    uint8_t  seq;          /* data-frame sequence, wraps at 256 */
    uint8_t  phase;        /* Send_Task cycles since last emission */
    uint8_t  slot;         /* 0..SUBSCRIBE_MAX_SLOTS-1; picks the frame type */
    uint8_t  due;          /* divider elapsed, waiting for a turn on the wire */
    uint16_t total_bytes;  /* sum of size*count over all ranges */
    Subscribe_Range_t ranges[SUBSCRIBE_MAX_STREAM_RANGES];
} Subscribe_Stream_t;

typedef struct {
    uint8_t         count;
    Subscribe_Tuple_t tuples[SUBSCRIBE_MAX_TUPLES];
} Subscribe_Request_t;

/* Validate one tuple. Returns 1 on accept, 0 on reject (writes a brief
 * reason into `err_out` up to err_cap bytes incl. NUL). */
uint8_t Subscribe_ValidateTuple(uint32_t address, uint16_t size,
                                char* err_out, uint16_t err_cap);

/* Parse a host->FC request frame into `req`. Frame layout:
 *   [0xCC] [0xDE] [CMD=0x20] [LEN_HI] [LEN_LO] [MAX_NUM_BASIS]
 *   [payload: count * 6 bytes]
 *   [CRC8 XOR]
 * Returns 1 on accept, 0 on reject. On reject, `err_out` is filled.
 *
 * `buf` is the raw staging buffer including the leading 0xCC 0xDE sync
 * bytes; `len` is the full frame length.
 */
uint8_t Subscribe_ParseRequest(const uint8_t* buf, uint16_t len,
                               Subscribe_Request_t* req,
                               char* err_out, uint16_t err_cap);

/* Build the 0x07 reply into `out` (file-scope static in subscribe.c).
 * Caller owns the buffer between DMA arming and DMA completion. Writes
 * the total frame length to `out_len`. Returns 1 on success, 0 if
 * `out_cap` is too small (won't happen with the 512 B file-scope
 * buffer — kept for defensive coding).
 */
uint8_t Subscribe_BuildReply(const Subscribe_Request_t* req,
                             uint8_t* out, uint16_t out_cap,
                             uint16_t* out_len);

/* Build the 0x7F error reply into `out`. `count` is echoed into the
 * MAX_NUM_BASIS slot so the host can correlate the error with its
 * request. `msg` is a NUL-terminated C string <= 240 bytes (truncated
 * silently if longer).
 */
uint8_t Subscribe_BuildError(const char* msg, uint8_t count,
                             uint8_t* out, uint16_t out_cap,
                             uint16_t* out_len);

/* ---- Streaming subscription API ---------------------------------------- */

/* Validate one range. Same allowlist rules as Subscribe_ValidateTuple, plus
 * count >= 1 and the whole span [address, address + size*count) must stay
 * inside the region it started in. Returns 1 on accept, 0 on reject. */
uint8_t Subscribe_ValidateRange(uint32_t address, uint16_t size, uint16_t count,
                                char* err_out, uint16_t err_cap);

/* Bytes per second this subscription puts on its transport, at the nominal
 * Send_Task rate. 0 when inactive. */
uint32_t Subscribe_StreamBps(const Subscribe_Stream_t* st);

/* Parse a host->FC 0x21 stream-subscribe request into `st`. Validates every
 * range, the total payload width against SUBSCRIBE_STREAM_MAX_BYTES, and the
 * resulting bandwidth against the selected transport's budget. `st->seq`,
 * `st->phase` and `st->due` are reset on accept. DIVIDER == 0 is accepted and
 * yields st->active == 0 (an explicit stop).
 *
 * `other_bps_uart5` / `other_bps_usart3` are the bandwidth already committed by
 * the OTHER slots on each transport; the guard adds this request to whichever
 * applies. Pass 0 for both to budget a single slot in isolation. Keeping this
 * as a parameter rather than reading the slot table keeps the function pure and
 * testable. Returns 1 on accept, 0 on reject. */
uint8_t Subscribe_ParseStreamRequest(const uint8_t* buf, uint16_t len,
                                     Subscribe_Stream_t* st,
                                     uint32_t other_bps_uart5,
                                     uint32_t other_bps_usart3,
                                     char* err_out, uint16_t err_cap);

/* Build the 0x08 schema frame describing an accepted subscription. */
uint8_t Subscribe_BuildSchema(const Subscribe_Stream_t* st,
                              uint8_t* out, uint16_t out_cap,
                              uint16_t* out_len);

/* Build one 0x09 data frame: values only, packed in range order. Reads live
 * memory, so the caller must not be holding the output buffer under DMA. */
uint8_t Subscribe_BuildStreamFrame(const Subscribe_Stream_t* st,
                                   uint8_t* out, uint16_t out_cap,
                                   uint16_t* out_len);

/* Called once per Send_Task cycle. Emits up to SUBSCRIBE_MAX_FRAMES_PER_TICK
 * 0x09 frames per tick; round-robin within the tick so a fast slot cannot
 * starve the others. No-op when no stream is active. */
void Subscribe_StreamTick(void);

/* 1 when a stream is running on USART3, so usart3_send() must stand down --
 * both share DMA1_Stream3 and would corrupt each other's buffer. */
uint8_t Subscribe_StreamOwnsUsart3(void);

#endif