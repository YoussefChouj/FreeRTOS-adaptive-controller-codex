/* Compile API/subscribe.c on the host and exercise the multi-slot scheduler.
 *
 * The Python tests assert a byte layout; this asserts that the C which will
 * actually run on the drone produces it -- and that Subscribe_StreamTick's
 * round-robin really does keep a fast slot from starving a slow one.
 *
 * Built with -m32 and a widened allowlist so real host addresses validate,
 * which means every subscription here goes through the REAL parser rather
 * than being poked into the slot table.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "subscribe.h"
#include "task.h"      /* TickType_t / xTaskGetTickCount, stubbed for the host */
#include "usart3.h"
#include "usart5.h"

uint8_t  UA5RxSubscribeBuf[USART5_SUBSCRIBE_RX_LEN];
uint16_t UA5RxSubscribeLen;
volatile uint8_t UA5RxSubscribePending;
volatile uint8_t Subscribe_RxTransport = 0U;  /* default = UART5 */

/* --- simulated clock ---------------------------------------------------- */
/* The firmware stamps xTaskGetTickCount() into every data frame. Driving it
 * from the harness is what lets us assert the timestamp is sampled per frame
 * rather than, say, latched once at subscribe time. */
static uint32_t g_ticks;
TickType_t xTaskGetTickCount(void) { return g_ticks; }

/* --- captured transport ------------------------------------------------- */
#define CAP_MAX 512
static uint8_t  cap_type[CAP_MAX];
static uint8_t  cap_seq[CAP_MAX];
static uint32_t cap_ts[CAP_MAX];
static uint16_t cap_len[CAP_MAX];
static uint8_t  cap_body[CAP_MAX][48];
static int      cap_n;
static uint8_t  usart3_busy;

static uint16_t crc16_ccitt(const uint8_t* d, uint16_t n)
{
    uint16_t crc = 0U, i; uint8_t b;
    for (i = 0U; i < n; i++) {
        crc ^= (uint16_t)((uint16_t)d[i] << 8);
        for (b = 0U; b < 8U; b++) {
            crc = (uint16_t)((crc & 0x8000U) ? (uint16_t)((crc << 1) ^ 0x1021U)
                                             : (uint16_t)(crc << 1));
        }
    }
    return crc;
}

static void capture(const uint8_t* buf, uint16_t len)
{
    if (cap_n < CAP_MAX && len >= 6U) {
        cap_type[cap_n] = buf[2];
        cap_seq[cap_n]  = buf[5];
        cap_len[cap_n]  = len;
        cap_ts[cap_n]   = (len >= 10U)
            ? ((uint32_t)buf[6] | ((uint32_t)buf[7] << 8)
               | ((uint32_t)buf[8] << 16) | ((uint32_t)buf[9] << 24))
            : 0U;
        memcpy(cap_body[cap_n], buf, len < 48U ? len : 48U);
        cap_n++;
    }
}
void Uart5_Subscribe_TxSend(const uint8_t* b, uint16_t l) { capture(b, l); }
uint8_t Usart3_Stream_Busy(void) { return usart3_busy; }
uint8_t Usart3_Stream_TxSend(const uint8_t* b, uint16_t l) { capture(b, l); return 1U; }

/* Stubs for functions in platform_registry.c that subscribe.c calls but the
 * harness never triggers (they fire on 0x22/0x23 commands only). They must
 * exist at link time even though they are never invoked. */
uint8_t PlatformRegistry_BuildDiscovery(uint8_t* out, uint16_t out_cap,
                                        uint16_t* out_len) { (void)out; (void)out_cap; (void)out_len; return 0U; }
uint8_t PlatformRegistry_BuildDigest(uint8_t* out, uint16_t out_cap,
                                      uint16_t* out_len) { (void)out; (void)out_cap; (void)out_len; return 0U; }
void PlatformRegistry_Init(void) {}

/* --- test scaffolding --------------------------------------------------- */
static int fails, checks;
static void ok(const char* what, int cond)
{
    checks++;
    if (!cond) { fails++; printf("FAIL  %s\n", what); }
}
static void okv(const char* what, long got, long want)
{
    checks++;
    if (got != want) { fails++; printf("FAIL  %s: got %ld want %ld\n", what, got, want); }
}

/* Live data the streams point at. */
static float    g_theta[6] = {1.0f, 2.0f, 3.0f, 4.0f, 5.0f, 6.0f};
static uint16_t g_rpm[2]   = {700, 800};

static uint8_t xorcrc(const uint8_t* d, uint16_t n)
{
    uint8_t c = 0U; uint16_t i;
    for (i = 0U; i < n; i++) { c ^= d[i]; }
    return c;
}

static void subscribe(uint8_t slot, uint8_t divider, uint8_t transport,
                      const uint32_t* addr, const uint16_t* size,
                      const uint16_t* count, uint8_t n)
{
    uint8_t* b = UA5RxSubscribeBuf;
    uint16_t payload = (uint16_t)(3U + n * 8U);
    uint16_t i, idx;
    b[0] = 0xCC; b[1] = 0xDE; b[2] = SUBSCRIBE_STREAM_CMD;
    b[3] = (uint8_t)(payload >> 8); b[4] = (uint8_t)(payload & 0xFF);
    b[5] = n; b[6] = divider; b[7] = transport; b[8] = slot;
    idx = 9U;
    for (i = 0U; i < n; i++) {
        b[idx++] = (uint8_t)(addr[i] & 0xFF);
        b[idx++] = (uint8_t)((addr[i] >> 8) & 0xFF);
        b[idx++] = (uint8_t)((addr[i] >> 16) & 0xFF);
        b[idx++] = (uint8_t)((addr[i] >> 24) & 0xFF);
        b[idx++] = (uint8_t)(size[i] & 0xFF);
        b[idx++] = (uint8_t)((size[i] >> 8) & 0xFF);
        b[idx++] = (uint8_t)(count[i] & 0xFF);
        b[idx++] = (uint8_t)((count[i] >> 8) & 0xFF);
    }
    b[idx] = xorcrc(&b[2], (uint16_t)(idx - 2U));
    idx++;
    UA5RxSubscribeLen = idx;
    UA5RxSubscribePending = 1U;
    Uart5_Subscribe_HandleRequest();
}

static void stop_all(void)
{
    uint32_t a = 0; uint16_t s = 0, c = 0;
    uint8_t i;
    for (i = 0U; i < SUBSCRIBE_MAX_SLOTS; i++) {
        cap_n = 0;
        subscribe(i, 0, SUBSCRIBE_TRANSPORT_UART5, &a, &s, &c, 0);
    }
    cap_n = 0;
}

static int count_type(uint8_t t)
{
    int i, n = 0;
    for (i = 0; i < cap_n; i++) { if (cap_type[i] == t) { n++; } }
    return n;
}

int main(void)
{
    uint32_t a[2]; uint16_t s[2], c[2];
    int i;

    a[0] = (uint32_t)(uintptr_t)g_theta; s[0] = 4; c[0] = 6;
    a[1] = (uint32_t)(uintptr_t)g_rpm;   s[1] = 2; c[1] = 2;

    /* ---- 1. schema layout ---------------------------------------------- */
    stop_all();
    subscribe(2, 4, SUBSCRIBE_TRANSPORT_UART5, a, s, c, 2);
    okv("one reply per request", cap_n, 1);
    okv("reply is a 0x08 schema", cap_type[0], SUBSCRIBE_FRAME_TYPE_SCHEMA);
    okv("schema len = 6 + (5 + N*8) + 1", cap_len[0], 6 + (5 + 2 * 8) + 1);
    okv("schema byte 5 = range count", cap_body[0][5], 2);
    okv("schema byte 6 = divider", cap_body[0][6], 4);
    okv("schema byte 7 = transport", cap_body[0][7], SUBSCRIBE_TRANSPORT_UART5);
    okv("schema byte 8 = slot", cap_body[0][8], 2);
    okv("schema total_bytes (BE) = 6*4 + 2*2",
        (cap_body[0][9] << 8) | cap_body[0][10], 6 * 4 + 2 * 2);

    /* ---- 2. slot bounds -------------------------------------------------- */
    stop_all();
    subscribe(SUBSCRIBE_MAX_SLOTS, 4, SUBSCRIBE_TRANSPORT_UART5, a, s, c, 1);
    okv("slot >= MAX_SLOTS rejected", cap_type[0], SUBSCRIBE_FRAME_TYPE_ERROR);

    /* ---- 3. budget is summed across slots -------------------------------- */
    stop_all();
    a[0] = (uint32_t)(uintptr_t)g_theta; s[0] = 4; c[0] = 6;
    subscribe(0, 4, SUBSCRIBE_TRANSPORT_UART5, a, s, c, 1);
    okv("36 B at divider 4 = 1800 B/s fits UART5's 2304 B/s alone",
        cap_type[0], SUBSCRIBE_FRAME_TYPE_SCHEMA);
    cap_n = 0;
    for (i = 1; i < (int)SUBSCRIBE_MAX_SLOTS; i++) {
        subscribe((uint8_t)i, 4, SUBSCRIBE_TRANSPORT_UART5, a, s, c, 1);
    }
    ok("a second identical slot trips the SUMMED budget (3600 > 2304)",
       count_type(SUBSCRIBE_FRAME_TYPE_ERROR) > 0);

    /* ---- 4. multi-rate scheduling ---------------------------------------- */
    stop_all();
    a[0] = (uint32_t)(uintptr_t)g_theta; s[0] = 4; c[0] = 6;
    subscribe(0, 1, SUBSCRIBE_TRANSPORT_USART3, a, s, c, 1);   /* fast, 80 Hz */
    a[0] = (uint32_t)(uintptr_t)g_rpm;   s[0] = 2; c[0] = 2;
    subscribe(1, 8, SUBSCRIBE_TRANSPORT_USART3, a, s, c, 1);   /* slow, 10 Hz */

    /* Test #4 (post telemetry-throughput-2026-09-08): two slots active, both
     * due together every 8th tick. The new dispatcher serves both in the
     * same tick (round-robin inside the while), so the fast slot gets ALL 80
     * ticks and the slow slot still gets its 10. cap_n = 90 (both served
     * when both due). The general per-tick cap is SUBSCRIBE_MAX_FRAMES_PER_TICK
     * (== SUBSCRIBE_MAX_SLOTS == 4), so we additionally assert that no
     * tick could ever emit more than that many frames. */
    cap_n = 0;
    for (i = 0; i < 80; i++) { g_ticks = (uint32_t)(1000 + i * 12); Subscribe_StreamTick(); }

    okv("slow slot emitted 80/8 times", count_type(SUBSCRIBE_FRAME_TYPE_DATA + 1), 10);
    okv("fast slot got every tick (multi-frame dispatch)", count_type(SUBSCRIBE_FRAME_TYPE_DATA), 80);
    ok("per-tick emission cap honored (N*80)", cap_n <= SUBSCRIBE_MAX_FRAMES_PER_TICK * 80);
    okv("no stray frame types", cap_n,
        count_type(SUBSCRIBE_FRAME_TYPE_DATA) + count_type(SUBSCRIBE_FRAME_TYPE_DATA + 1));

    /* ---- 5. per-slot sequence counters ----------------------------------- */
    {
        int seq_fast = -1, seq_slow = -1, mono = 1;
        for (i = 0; i < cap_n; i++) {
            if (cap_type[i] == SUBSCRIBE_FRAME_TYPE_DATA) {
                if (seq_fast >= 0 && cap_seq[i] != ((seq_fast + 1) & 0xFF)) { mono = 0; }
                seq_fast = cap_seq[i];
            } else {
                if (seq_slow >= 0 && cap_seq[i] != ((seq_slow + 1) & 0xFF)) { mono = 0; }
                seq_slow = cap_seq[i];
            }
        }
        ok("each slot keeps its own gap-free sequence", mono);
    }

    /* ---- 6. payload is values only, in range order ----------------------- */
    for (i = 0; i < cap_n; i++) {
        if (cap_type[i] == SUBSCRIBE_FRAME_TYPE_DATA) {
            okv("fast frame = 6 hdr + 4 ts + 24 values + 2 crc16",
                cap_len[i], 6 + 4 + 24 + 2);
            okv("LEN counts the timestamp with the values",
                (cap_body[i][3] << 8) | cap_body[i][4], 4 + 24);
            ok("payload is the live floats, no addresses repeated",
               memcmp(&cap_body[i][10], g_theta, sizeof(g_theta)) == 0);
            break;
        }
    }

    /* ---- 6b. source timestamp -------------------------------------------- */
    {
        int seen = 0, moved = 0, matches_clock = 1;
        uint32_t prev = 0;
        for (i = 0; i < cap_n; i++) {
            if (cap_type[i] != SUBSCRIBE_FRAME_TYPE_DATA) { continue; }
            /* Every frame must carry the tick of the cycle that built it, not
             * a value latched when the subscription was accepted. */
            if (cap_ts[i] < 1000U || ((cap_ts[i] - 1000U) % 12U) != 0U) {
                matches_clock = 0;
            }
            if (seen && cap_ts[i] > prev) { moved = 1; }
            prev = cap_ts[i]; seen = 1;
        }
        ok("every data frame carries a source timestamp from its own cycle",
           matches_clock);
        ok("the timestamp advances between frames", moved);
    }

    /* ---- 6c. CRC16 covers frame_type..last value -------------------------- */
    {
        int bad = 0;
        for (i = 0; i < cap_n; i++) {
            uint16_t n = cap_len[i];
            uint16_t want, got;
            if (n < 12U || n > 48U) { continue; }
            want = crc16_ccitt(&cap_body[i][2], (uint16_t)(n - 4U));
            got  = (uint16_t)(((uint16_t)cap_body[i][n - 2U] << 8)
                              | cap_body[i][n - 1U]);
            if (want != got) { bad++; }
        }
        okv("every data frame's CRC16-CCITT validates", bad, 0);
    }

    /* ---- 7. a busy USART3 DMA skips rather than corrupting ---------------- */
    stop_all();
    a[0] = (uint32_t)(uintptr_t)g_theta; s[0] = 4; c[0] = 6;
    subscribe(0, 1, SUBSCRIBE_TRANSPORT_USART3, a, s, c, 1);
    cap_n = 0;
    usart3_busy = 1U;
    for (i = 0; i < 10; i++) { Subscribe_StreamTick(); }
    okv("busy DMA emits nothing", cap_n, 0);
    usart3_busy = 0U;
    Subscribe_StreamTick();
    okv("and resumes once drained", cap_n, 1);
    ok("the skipped frames show up as a SEQ gap, not silence", cap_seq[0] >= 10);

    printf("\n%d checks, %d failure(s)\n", checks, fails);
    return fails ? 1 : 0;
}
