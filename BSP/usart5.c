#include "usart5.h"
#include "subscribe.h"   /* SUBSCRIBE_CMD / SUBSCRIBE_STREAM_CMD payload shapes */
#include "platform_registry.h"
#include "command_protocol.h"
#include "gs_command.h"
#include "fw_identity.h"

/* Forward declaration: try_stage_subscribe_frame is defined below
 * handle_subscribe_frame but called from it. ARMCC C90 requires
 * declarations before use. */
static uint8_t try_stage_subscribe_frame(const uint8_t* mailbox, uint16_t off,
                                         uint16_t frame_len, uint16_t* offset);

/**
 * @module  usart5.c
 * @subsystem  comm
 * @depends  usart5.h, subscribe.h
 * @owns  UART5 DMA setup and UART5 ground-station command ingress
 * @caution  command frame parsing must stay byte-compatible with host serializer and UART4 ingress path
 */

//   rx pd2  
//   tx pc12

UCHAR8 UA5RxDMAbuf[USART5_RXDMA_LEN] = {0};
UCHAR8 UA5RxMailbox[USART5_RXMB_LEN] = {0};
/* UART5 extended-prefix (0xCC 0xDE) subscribe request staging buffer. File-scope:
 * Send_Task stack is 500 words / 2 kB; the largest legal request is
 * 6 (header) + 32 * 6 (tuples) + 1 (CRC) = 199 B, too big for any stack local.
 * `UA5RxSubscribeLen` is set by the IRQ-side parser when a complete 0xCC 0xDE
 * frame lands; Send_Task picks it up off the back of the UART5 DMA hand-off in
 * TASK/send_data.c. */
UCHAR8 UA5RxSubscribeBuf[USART5_SUBSCRIBE_RX_LEN] = {0};
volatile uint16_t UA5RxSubscribeLen = 0U;
/* Set by the IRQ-side parser when a complete, CRC-valid 0xCC 0xDE subscribe
 * request lands. Cleared by Send_Task after the reply DMA completes (or by
 * the validator if the request is rejected with a 0x7F error reply). */
volatile uint8_t UA5RxSubscribePending = 0U;
/* Last transport that delivered a subscribe request. UART5 is the safe
 * default before any request arrives. */
volatile uint8_t Subscribe_RxTransport = SUBSCRIBE_RX_TRANSPORT_UART5;
/* Transport of the staged request, latched under the same mask as the frame.
 * Subscribe_RxTransport tracks the *current* ingress and can be overwritten by
 * a later UART5 frame before Send_Task replies; the reply must use this one. */
volatile uint8_t UA5RxSubscribeTransport = SUBSCRIBE_RX_TRANSPORT_UART5;
USART_RX_TypeDef UART5_Rcr = {UART5,UART5_RX_STREAM,UA5RxMailbox,UA5RxDMAbuf,USART5_RXMB_LEN,USART5_RXDMA_LEN,0,0,0};

void UART5_Configuration(void)
{
		USART_InitTypeDef uart5;
		GPIO_InitTypeDef  GPIO_InitStructure;
		NVIC_InitTypeDef  nvic;
		DMA_InitTypeDef   DMA_InitStructure;

		RCC_AHB1PeriphClockCmd( RCC_AHB1Periph_DMA1,ENABLE);//ʹ��PC�˿�ʱ��
	  RCC_APB1PeriphClockCmd(RCC_APB1Periph_UART5, ENABLE); //����USART2ʱ��
    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOC, ENABLE);
    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOD, ENABLE);

		GPIO_PinAFConfig(GPIOC,GPIO_PinSource12,GPIO_AF_UART5); 
		GPIO_PinAFConfig(GPIOD,GPIO_PinSource2,GPIO_AF_UART5); 

    //����PC12��ΪUART5��Tx
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_12;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_InitStructure.GPIO_OType = GPIO_OType_PP;
    GPIO_InitStructure.GPIO_PuPd = GPIO_PuPd_UP;
    GPIO_Init(GPIOC, &GPIO_InitStructure);
    //����PD2��ΪUART5��Rx
    GPIO_InitStructure.GPIO_Pin = GPIO_Pin_2;
    GPIO_InitStructure.GPIO_Mode = GPIO_Mode_AF;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_InitStructure.GPIO_OType = GPIO_OType_OD;
    GPIO_InitStructure.GPIO_PuPd = GPIO_PuPd_NOPULL;
    GPIO_Init(GPIOD, &GPIO_InitStructure);


		nvic.NVIC_IRQChannel = UART5_IRQn;
		nvic.NVIC_IRQChannelPreemptionPriority = 5;//��ռ���ȼ�  (matches configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY -- below the FreeRTOS syscall ceiling, maskable by critical sections; see stack-hardening spec §3.4)
		nvic.NVIC_IRQChannelSubPriority = 0;//�����ȼ�
		nvic.NVIC_IRQChannelCmd = ENABLE;//IRQͨ��ʹ�� 
		NVIC_Init(&nvic);//����ָ���Ĳ�����ʼ��VIC�Ĵ���

		uart5.USART_BaudRate = 115200;//������ (ground station)
		uart5.USART_WordLength = USART_WordLength_8b;//�ֳ�Ϊ8λ���ݸ�ʽ
		uart5.USART_StopBits = USART_StopBits_1;//һ��ֹͣλ
		uart5.USART_Parity = USART_Parity_No;//����żУ��λ
		uart5.USART_Mode = USART_Mode_Rx|USART_Mode_Tx;//������
		uart5.USART_HardwareFlowControl = USART_HardwareFlowControl_None;//��Ӳ������������
		USART_Init(UART5,&uart5);//��ʼ������

		USART_DMACmd(UART5,USART_DMAReq_Rx,ENABLE);
		USART_DMACmd(UART5,USART_DMAReq_Tx,ENABLE);
		USART_ITConfig(UART5,USART_IT_IDLE,ENABLE); //���������ж�

		USART_Cmd(UART5,ENABLE);//ʹ�ܴ���

		DMA_DeInit(DMA1_Stream0);
		DMA_InitStructure.DMA_Channel= DMA_Channel_4;//ͨ��
		DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(UART5->DR);//�����ַ
		DMA_InitStructure.DMA_Memory0BaseAddr = (uint32_t)UA5RxDMAbuf;//������4���յ�������ucRxData_DMA1_Stream2[]��ڴ����ַ
		DMA_InitStructure.DMA_DIR = DMA_DIR_PeripheralToMemory;//�������ݴ��䷽��
		DMA_InitStructure.DMA_BufferSize = USART5_RXDMA_LEN;//����DMAһ�δ����������Ĵ�С
		DMA_InitStructure.DMA_PeripheralInc = DMA_PeripheralInc_Disable;//���������ַ����
		DMA_InitStructure.DMA_MemoryInc = DMA_MemoryInc_Enable;	//�����ڴ��ַ����
		DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;//������������ݳ���Ϊ�ֽڣ�8bits��
		DMA_InitStructure.DMA_MemoryDataSize = DMA_MemoryDataSize_Byte;//�����ڴ�����ݳ���Ϊ�ֽڣ�8bits��
		DMA_InitStructure.DMA_Mode = DMA_Mode_Circular;//DMA_Mode_Normal;////����DMAģʽΪѭ��ģʽ
		DMA_InitStructure.DMA_Priority = DMA_Priority_VeryHigh;//DMA_Priority_Medium;//����DMAͨ�������ȼ�Ϊ������ȼ�
		DMA_InitStructure.DMA_FIFOMode = DMA_FIFOMode_Disable;
		DMA_InitStructure.DMA_FIFOThreshold = DMA_FIFOThreshold_Full;
		DMA_InitStructure.DMA_MemoryBurst = DMA_MemoryBurst_Single;
		DMA_InitStructure.DMA_PeripheralBurst = DMA_PeripheralBurst_Single;
		DMA_Init(DMA1_Stream0,&DMA_InitStructure);

		//DMA_ITConfig(DMA1_Stream2,DMA_IT_TC,ENABLE);
		DMA_Cmd(DMA1_Stream0,ENABLE);

		/////////////////////////TX
		DMA_InitTypeDef		dma;
		DMA_DeInit(DMA1_Stream7);
		while( DMA_GetCmdStatus(DMA1_Stream7) == ENABLE );			//�ȴ�DMA������

		dma.DMA_Channel				=	DMA_Channel_4;
		dma.DMA_PeripheralBaseAddr	=	(uint32_t)&(UART5->DR);
		dma.DMA_Memory0BaseAddr		=	NULL;//����
		dma.DMA_DIR					=	DMA_DIR_MemoryToPeripheral;	//�ڴ浽����
		dma.DMA_BufferSize			=	NULL;//����
		dma.DMA_PeripheralInc		=	DMA_PeripheralInc_Disable;
		dma.DMA_MemoryInc			=	DMA_MemoryInc_Enable;
		dma.DMA_PeripheralDataSize	=	DMA_PeripheralDataSize_Byte;
		dma.DMA_MemoryDataSize		=	DMA_MemoryDataSize_Byte;
		dma.DMA_Mode				=	DMA_Mode_Normal;			//��������
		dma.DMA_Priority			=	DMA_Priority_VeryHigh;
		dma.DMA_FIFOMode			=	DMA_FIFOMode_Disable;
		dma.DMA_FIFOThreshold		=	DMA_FIFOThreshold_1QuarterFull;
		dma.DMA_MemoryBurst			=	DMA_MemoryBurst_Single;
		dma.DMA_PeripheralBurst		=	DMA_PeripheralBurst_Single;
		DMA_Init(DMA1_Stream7, &dma);
		DMA_Cmd(DMA1_Stream7, DISABLE);
}

extern volatile GS_Cmd_t gs_cmd_queue[16];
extern volatile uint8_t gs_cmd_head;
extern volatile uint8_t gs_cmd_tail;
extern volatile uint32_t gs_cmd_drop_count;

/* Second DMA1_Stream7 turn for the 0x07 / 0x7F reply. Caller (API/subscribe.c
 * via Send_Task) is responsible for ensuring the live telemetry DMA has
 * completed; the existing pattern in Send_Groundstation_Telemetry_UART4 is
 * `while (DMA_GetCurrDataCounter(DMA1_Stream7));` followed by clearing the
 * stream-7 flags. Mirrors that exact sequence so the second turn does not
 * corrupt the live telemetry burst. */
/* Gated on SUBSCRIBE_UART5_ENABLED (default 0): when disabled the UART5
 * subscribe path is dormant and all subscribe requests come in over USART3.
 * The symbol stays alive so SendReplyToTransport's else-branch call
 * compiles without conditional compilation. */
#if SUBSCRIBE_UART5_ENABLED
void Uart5_Subscribe_TxSend(const uint8_t* buf, uint16_t len)
{
    uint32_t to = 50000U;

    if ((buf == 0) || (len == 0U)) {
        return;
    }
    while (DMA_GetCurrDataCounter(DMA1_Stream7) && --to);
    if (!to) {
        return;
    }
    DMA_Cmd(DMA1_Stream7, DISABLE);
    while (DMA_GetCmdStatus(DMA1_Stream7) == ENABLE);
    DMA_ClearFlag(DMA1_Stream7, DMA_FLAG_TCIF7 | DMA_FLAG_HTIF7 | DMA_FLAG_TEIF7
                              | DMA_FLAG_DMEIF7 | DMA_FLAG_FEIF7);
    DMA1_Stream7->M0AR = (uint32_t)buf;
    DMA1_Stream7->NDTR = len;
    DMA_Cmd(DMA1_Stream7, ENABLE);
}
#else
void Uart5_Subscribe_TxSend(const uint8_t* buf, uint16_t len)
{
    (void)buf;
    (void)len;
}
#endif

/* Transport-agnostic GS command-frame parser. Exported so BSP/usart3.c can call
 * the same parser from the USART3 IDLE handler (USART3_IRQHandler ->
 * Handle_USART3_GroundStation_Command). Internally static; extern declared in
 * BSP/usart5.h so the usart3.c caller needs no direct knowledge of it.
 *
 * allow_subscribe: pass 1 to accept 0xCC 0xDE subscribe requests (UART5 path).
 * Pass 0 to advance past 0xCC 0xDE without touching UART5-only state
 * (USART3 path, which stages via Handle_USART3_GroundStation_Command).
 *
 * Split into a thin dispatch loop + two frame-type helpers
 * (handle_subscribe_frame, handle_command_frame) per stack-hardening spec
 * §4.3: the original single-function shape ran at CCN 22 / NLOC 95 (lizard)
 * because both branches shared the while body, and the §3.5/§3.6 bounds check
 * and PRIMASK guard pushed it past 24. Splitting per branch drops each helper
 * well under the CCN<=12 / NLOC<=60 gate. */

/* Returns 1 when the caller should `break` out of the parse loop (frame is
 * truncated and the remainder belongs to the next IDLE). The helper advances
 * *offset internally for every other outcome (valid frame: frame_len bytes;
 * malformed frame: 1 byte for resync). */
static uint8_t handle_subscribe_frame(const uint8_t* mailbox, uint16_t total,
                                       uint16_t* offset)
{
	uint16_t off = *offset;
	// Extended-prefix subscribe request: variable payload, run-length encoded.
	// Header layout (offsets inside mailbox):
	//   +0  0xCC (SYNC_HI)
	//   +1  0xDE (SYNC_LO)
	//   +2  CMD (0x20)
	//   +3  LEN_HI
	//   +4  LEN_LO
	//   +5  MAX_NUM_BASIS (tuple count)
	//   +6 .. +6+LEN-1  payload tuples (each 6 B)
	//   +6+LEN         CRC8 XOR
	// Minimum frame is 7 B (zero tuples: 6 header + 0 payload + 1 CRC).
	uint8_t  sub_cmd = mailbox[off + 2U];
	uint16_t len_hi = mailbox[off + 3U];
	uint16_t len_lo = mailbox[off + 4U];
	uint16_t payload_len = (uint16_t)((len_hi << 8) | len_lo);
	uint8_t  len_ok;
	// Reject malformed frames early: total in mailbox must cover header + payload + 1 CRC.
	// Two commands share the 0xCC 0xDE prefix and differ in payload shape:
	//   0x20 one-shot read   payload = N * 6   (address LE32 + size LE16)
	//   0x21 stream subscribe payload = 2 + N * 8 (divider, transport, then
	//                                             address LE32 + size LE16 + count LE16)
	// Anything else is not a frame we own; skip a byte and resync.
	if (sub_cmd == SUBSCRIBE_STREAM_CMD)
	{
		// 0x21 payload = 3 config bytes (divider, transport, slot) + N * 8.
		len_ok = ((payload_len >= 3U) && (((payload_len - 3U) % 8U) == 0U)) ? 1U : 0U;
	}
	else if (sub_cmd == SUBSCRIBE_CMD)
	{
		len_ok = ((payload_len % 6U) == 0U) ? 1U : 0U;
	}
	else if (PlatformRegistry_IsDiscoveryCommand(sub_cmd) != 0U)
	{
		len_ok = ((payload_len == 0U) && (mailbox[off + 5U] == 0U)) ? 1U : 0U;
	}
	else if (sub_cmd == FW_IDENTITY_CMD)
	{
		len_ok = ((payload_len == 0U) && (mailbox[off + 5U] == 0U)) ? 1U : 0U;
	}
	else
	{
		len_ok = 0U;
	}
	if ((len_ok == 0U) ||
	    payload_len > (USART5_SUBSCRIBE_RX_LEN - 7U))
	{
		*offset = (uint16_t)(off + 1U);
		return 0U;
	}
	uint16_t frame_len = (uint16_t)(6U + payload_len + 1U);
	if (off + frame_len > total)
	{
		// Truncated: stop walking, leave the partial frame for the next IDLE.
		return 1U;
	}
	if (try_stage_subscribe_frame(mailbox, off, frame_len, offset) != 0U)
	{
		// Bounds-check failure (frame_len > staging buffer): handler already
		// advanced *offset by 1 for resync. Continue walking.
		return 0U;
	}
	*offset = (uint16_t)(off + frame_len);
	return 0U;
}

/* Returns 1 if the caller should advance *offset by 1 (the frame was rejected
 * by the bounds check or CRC validation failed and we want to skip just this
 * byte and resync). Returns 0 when the frame was staged (or silently dropped
 * because the mailbox slot was already occupied, which the caller treats as
 * "advance by frame_len").
 *
 * Caller has already verified the prefix and that the frame fits in the
 * mailbox. We re-check frame_len against USART5_SUBSCRIBE_RX_LEN as a
 * defensive upper bound (stack-hardening spec §3.5) and run the CRC8 XOR
 * validation. Staging is PRIMASK-guarded (spec §3.6) to close the TOCTOU
 * window with Send_Task. */
static uint8_t try_stage_subscribe_frame(const uint8_t* mailbox, uint16_t off,
                                           uint16_t frame_len, uint16_t* offset)
{
	if (frame_len > USART5_SUBSCRIBE_RX_LEN)
	{
		// Defensive: a future frame-format change pushed the frame past the
		// staging buffer. Resync instead of writing torn bytes.
		*offset = (uint16_t)(off + 1U);
		return 1U;
	}
	uint8_t calc_crc = 0;
	uint16_t i;
	for (i = 2U; i < (uint16_t)(frame_len - 1U); i++)
	{
		calc_crc ^= mailbox[off + i];
	}
	uint8_t crc = mailbox[off + frame_len - 1U];
	if (calc_crc != crc)
	{
		return 0U;
	}
	// Stage the validated frame for Send_Task. Copy into the file-scope
	// UA5RxSubscribeBuf (NOT a stack local — Send_Task = 500 words / 2 kB).
	// The IRQ path does NOT arm the reply DMA; that happens off the back
	// of the live-telemetry DMA hand-off in TASK/send_data.c, so the
	// reply observes the same UART5 timing contract as A/B telemetry.
	// If a previous reply is still pending (i.e. Send_Task hasn't picked
	// it up yet), drop the new request silently — protects against a
	// runaway host that re-sends faster than 60 Hz Send_Task cadence.
	/* Subscribe_RxTransport was already set by the ingress wrapper
	 * (Handle_UART5_GroundStation_Command or Handle_USART3_GroundStation_
	 * Command) before calling this parser. Do NOT overwrite it here.
	 * (UA5RxSubscribePending is a single-slot mailbox: only one
	 * staged request lives at a time.) */
	/* PRIMASK save/restore (stack-hardening spec §3.6): the previous
	 * check-then-set was TOCTOU -- Send_Task or a higher-priority ISR
	 * could have re-entered the staging region between the test and
	 * the copy. Masking interrupts around the whole check-copy-set
	 * closes the window. 256 B copy at 168 MHz takes < 2 us; the
	 * masking cost is negligible against the 10.9 us character time. */
	uint32_t pri = __get_PRIMASK();
	__disable_irq();
	if (UA5RxSubscribePending == 0U)
	{
		for (i = 0U; i < frame_len; i++)
		{
			UA5RxSubscribeBuf[i] = mailbox[off + i];
		}
		UA5RxSubscribeLen = frame_len;
		UA5RxSubscribeTransport = Subscribe_RxTransport;
		UA5RxSubscribePending = 1U;
	}
	__set_PRIMASK(pri);
	return 0U;
}

/* Returns 1 if a 0xCC 0xDD command frame was consumed (offset advanced by 9),
 * 0 if the prefix does not match (caller resyncs). */
static uint8_t handle_command_frame(const uint8_t* mailbox, uint16_t* offset)
{
	uint16_t off = *offset;
	if (mailbox[off] != 0xCC || mailbox[off + 1U] != 0xDD)
	{
		return 0U;
	}
	uint8_t cmd_id = mailbox[off + 2U];
	uint8_t index  = mailbox[off + 3U];

	union {
		float f;
		uint8_t b[4];
	} val;

	val.b[0] = mailbox[off + 4U];
	val.b[1] = mailbox[off + 5U];
	val.b[2] = mailbox[off + 6U];
	val.b[3] = mailbox[off + 7U];

	uint8_t crc = mailbox[off + 8U];
	uint8_t calc_crc = 0;
	int i;
	for (i = 2; i < 8; i++) {
		calc_crc ^= mailbox[off + (uint16_t)i];
	}

	if (calc_crc == crc) {
		uint8_t next_head = (uint8_t)((gs_cmd_head + 1U) % 16U);
		if (next_head != gs_cmd_tail) {
			gs_cmd_queue[gs_cmd_head].id    = cmd_id;
			gs_cmd_queue[gs_cmd_head].index = index;
			gs_cmd_queue[gs_cmd_head].value = val.f;
			gs_cmd_queue[gs_cmd_head].transaction_id = 0U;
			gs_cmd_queue[gs_cmd_head].transaction_flags = 0U;
			gs_cmd_queue[gs_cmd_head].transaction_transport = 0U;
			gs_cmd_head = next_head;
		} else {
			gs_cmd_drop_count++;
		}
	}
	*offset = (uint16_t)(off + 9U);
	return 1U;
}

/* Parse the versioned S3 command envelope. Valid transactions are projected
 * into the existing command queue; the transaction metadata travels with the
 * record so Send_Task can emit correlated outcomes after dispatch. */
static uint8_t handle_transaction_frame(const uint8_t* mailbox, uint16_t total,
                                         uint16_t* offset)
{
	uint16_t off = *offset;
	uint16_t payload_len;
	uint16_t frame_len;
	PlatformCommand_t command;
	PlatformCommandStatus_e status;
	union { float f; uint8_t b[4]; } val;
	uint8_t next_head;
	uint8_t i;

	if ((mailbox[off] != PLATFORM_COMMAND_SYNC_HI) ||
	    (mailbox[off + 1U] != PLATFORM_COMMAND_SYNC_LO)) {
		return 0U;
	}
	if ((uint16_t)(total - off) < 10U) {
		return 1U;
	}
	payload_len = (uint16_t)mailbox[off + 8U] |
	              ((uint16_t)mailbox[off + 9U] << 8);
	frame_len = (uint16_t)(11U + payload_len);
	if ((frame_len < 15U) || ((uint16_t)(total - off) < frame_len)) {
		return 1U;
	}
	status = PlatformCommand_Parse(&mailbox[off], frame_len, &command);
	if ((status == PLATFORM_COMMAND_OK) && (command.payload_len == 4U)) {
		for (i = 0U; i < 4U; i++) {
			val.b[i] = command.payload[i];
		}
		next_head = (uint8_t)((gs_cmd_head + 1U) % 16U);
		if (next_head != gs_cmd_tail) {
			gs_cmd_queue[gs_cmd_head].id = command.command_id;
			gs_cmd_queue[gs_cmd_head].index = command.index;
			gs_cmd_queue[gs_cmd_head].value = val.f;
			gs_cmd_queue[gs_cmd_head].transaction_id = command.transaction_id;
			gs_cmd_queue[gs_cmd_head].transaction_flags = command.flags;
			gs_cmd_queue[gs_cmd_head].transaction_transport = Subscribe_RxTransport;
			gs_cmd_head = next_head;
		} else {
			gs_cmd_drop_count++;
		}
	}
	*offset = (uint16_t)(off + frame_len);
	return 0U;
}

static void ParseGsCommandFrames(const uint8_t* mailbox, uint16_t total,
                                  uint8_t allow_subscribe)
{
	uint16_t offset = 0;
	while (offset + 7U <= total)
	{
		if (allow_subscribe != 0U &&
		    mailbox[offset] == 0xCC && mailbox[offset + 1U] == 0xDE)
		{
			if (handle_subscribe_frame(mailbox, total, &offset) != 0U)
			{
				break;
			}
		}
		else if ((mailbox[offset] == PLATFORM_COMMAND_SYNC_HI) &&
		         (mailbox[offset + 1U] == PLATFORM_COMMAND_SYNC_LO))
		{
			if (handle_transaction_frame(mailbox, total, &offset) != 0U)
			{
				break;
			}
		}
		else if ((offset + 9U) > total)
		{
			break;
		}
		else if (handle_command_frame(mailbox, &offset) == 0U)
		{
			offset = (uint16_t)(offset + 1U);
		}
	}
}

/* UART5 ingress wrapper. Source of truth for the 0xCC 0xDE subscribe path --
 * only UART5 has the staging buffer (UA5RxSubscribeBuf) and the reply DMA
 * (DMA1_Stream7) the subscribe handler arms off the back of Send_Task. */
/* Gated on SUBSCRIBE_UART5_ENABLED (default 0): when disabled, UART5 carries
 * no subscribe control plane and the USART3 parser (BSP/usart3.c) handles all
 * 0xCC 0xDE requests instead. */
#if SUBSCRIBE_UART5_ENABLED
void Handle_UART5_GroundStation_Command(void)
{
	Subscribe_RxTransport = SUBSCRIBE_RX_TRANSPORT_UART5;
	ParseGsCommandFrames(UA5RxMailbox, UART5_Rcr.rxSize, 1U);
}
#else
void Handle_UART5_GroundStation_Command(void)
{
	/* UART5 subscribe disabled: USART3 owns all subscribe ingress. */
	(void)0;
}
#endif

/* USART3 ingress wrapper. Exported (BSP/usart5.h) for USART3_IRQHandler in
 * TASK/stm32f4xx_it.c. Reads UA3RxMailbox via the parameter so the parser has
 * no UART5-specific state in its body.
 *
 * allow_subscribe=1 (2026-08-20, unified_subscribe_lane): the 0xCC 0xDE
 * subscribe path was previously UART5-only. This call now stages requests into
 * UA5RxSubscribeBuf and sets Subscribe_RxTransport=USART3 so the reply
 * (0x08 / 0x7F) goes back over the same USART3/WiFi link the request arrived
 * on. The existing 0xCC 0xDD command path (CMD 0x01..0x18) is unchanged. */
void Handle_USART3_GroundStation_Command(const uint8_t* mailbox, uint16_t total)
{
	Subscribe_RxTransport = SUBSCRIBE_RX_TRANSPORT_USART3;
	ParseGsCommandFrames(mailbox, total, 1U);
}
