#ifndef  __USART3_H__
#define  __USART3_H__

#include "stm32f4xx.h"
#include "main.h"

/* UART baud toward the long-range radio module. HARDWARE REPLACED 2026-07-31: the
 * 24RF/nRF24L01 pair is gone; USART3 now drives a 2.4 GHz BLE serial module (Yuanxi),
 * configured to 921600 on BOTH ends with its Windows tool. The old 115200 figure and
 * the "no AT command can change the baud" conclusion belonged to the retired hardware.
 *
 * MEASURED 2026-07-31, not guessed: USART3->BRR was swept live over SWD while the
 * ground dongle was captured. Only at 921600 does the payload survive -- 218 distinct
 * byte values and the full 59-float test ramp verbatim; every lower rate collapses the
 * stream to 1-2 distinct byte values and zero tails.
 *
 * APB1 is 42 MHz, so 921600 is not exactly representable: BRR = 0x2E gives 913043 baud,
 * -0.93%. Well inside 8N1 tolerance (~2%) and confirmed working on the wire.
 *
 * Lives in the header (not usart3.c) because API/subscribe.c sizes its stream
 * link-budget guard from it. The radio is no longer the binding constraint:
 * MEASURED 2026-08-09 the MicoAir WiFi module carried 55116 B/s at 0.00% loss
 * and its knee was never reached, so USART3_BAUD/10 = 91304 B/s is now the real
 * ceiling and the budget guard is conservative. See TASK/send_data.c. */
#define USART3_BAUD                921600

void USART3_Configuration(void);
extern UCHAR8 Custom_DataBuf[68];

/* RX mailbox / DMA buffer sizes. DMA ring 256 B coalesces up to 28 9-byte
 * 0xCC 0xDD commands across one IDLE. Mailbox bumped 2026-09-08 from 96 to
 * 256 to hold the worst-case 0xCC 0xDE subscribe request: header 6 B + 24
 * range tuples * 8 B + CRC 1 B = 199 B, rounded up to 256 with margin so the
 * IDLE handler can stage the whole frame in one shot. Previously a 263 B
 * DMA burst overran a 96 B mailbox and corrupted whatever sat after it in
 * BSS -- this was the prime suspect in the recent HardFault incident
 * (subscribe audit Finding 1). BSS growth: +160 B (1.6 % of 192 KB RAM). */
#define USART3_RXDMA_LEN           256
#define USART3_RXMB_LEN            256

/* ---- continuous TX ring ------------------------------------------------
 * 3.6x the largest frame the throughput ladder emits (1124 B). The top rung
 * offers 90145 B/s against a 91304 B/s wire, i.e. only 1.3% drain margin, so
 * the ring's whole job there is to absorb Send_Task's jitter: that task is
 * preempted by the 1 kHz IMU and 200 Hz control loops, and two frames landing
 * back to back must not be scored as loss. 2048 B held only 1.8 frames, which
 * is too thin for that. One byte stays unused so head==tail means "empty". */
#define USART3_TX_RING_LEN         4096U

/* Queue a frame for transmission. Copies into the TX ring and returns
 * immediately; DMA1_Stream3 streams the ring out continuously, so the caller's
 * buffer is free the moment this returns.
 *
 * Returns 1 if the WHOLE frame was queued, 0 if it did not fit (in which case
 * nothing at all was written -- a partial frame would put a torn record on the
 * wire, which the host would score as corruption rather than as loss).
 *
 * REPLACED 2026-08-09 a one-frame-per-tick hand-off that armed the DMA directly
 * and returned 0 whenever the previous transfer was still draining. That guard
 * made frame size, not byte rate, the limit: at 884 B the transfer straddled the
 * Send_Task tick, every other tick was skipped, and the measured cadence
 * collapsed 80.2 -> 48.3 Hz even though the radio was losing nothing (0.00% at
 * every rung). Buffering instead of skipping decouples emission from the tick
 * and lets the link run to the UART wire rate. */
uint8_t Usart3_Stream_TxSend(const uint8_t* buf, uint16_t len);

/* Backpressure, NOT "a transfer is in flight" -- under continuous streaming the
 * DMA is almost always busy, so the old meaning would starve any caller that
 * polled it. Returns 1 when the ring is over half full, i.e. the link is not
 * keeping up and the caller should skip this cycle. */
uint8_t Usart3_Stream_Busy(void);

/* DMA1_Stream3 transfer-complete hook. Called from DMA1_Stream3_IRQHandler
 * (TASK/stm32f4xx_it.c); advances the ring's tail and arms the next chunk. */
void Usart3_Tx_DmaIsr(void);

/* Instrumentation, read over livewatch. Non-static so DWARF resolves them.
 *  UA3TxFrames - frames accepted into the ring
 *  UA3TxDrops  - frames rejected for lack of space (FC-side loss, NOT air loss)
 *  UA3TxPeak   - high-water mark of ring occupancy, bytes */
extern volatile uint32_t UA3TxFrames;
extern volatile uint32_t UA3TxDrops;
extern volatile uint16_t UA3TxPeak;

/* RX instrumentation + command-dispatch ingress. USART3_IRQHandler
 * (TASK/stm32f4xx_it.c) drains the IDLE-coalesced bytes into UA3RxMailbox
 * via DMA1_Stream1's circular buffer, increments the counters, then calls
 * Handle_USART3_GroundStation_Command with the bytes. Sizes set in BSP/usart3.c. */
extern volatile uint32_t UA3RxFrameCnt;
extern volatile uint16_t UA3RxLastLen;
extern UCHAR8 UA3RxMailbox[USART3_RXMB_LEN];
extern USART_RX_TypeDef USART3_Rcr;
#endif
