#include "usart3.h"

/* USART3_BAUD now lives in usart3.h -- API/subscribe.c needs it to size the
 * stream link-budget guard. The RX sizes live there too, so USART3_Rcr below
 * references the header's USART3_RXDMA_LEN / USART3_RXMB_LEN. */
__IO UCHAR8 UA3RxDMAbuf[USART3_RXDMA_LEN] = {0};
     UCHAR8 UA3RxMailbox[USART3_RXMB_LEN] = {0};
USART_RX_TypeDef USART3_Rcr = {USART3,DMA1_Stream1,UA3RxMailbox,UA3RxDMAbuf,USART3_RXMB_LEN,USART3_RXDMA_LEN,0,0,0};

 UCHAR8 Custom_DataBuf[68] = {0};

/* USART3 RX instrumentation + 0xCC 0xDD command-dispatch ingress.
 *
 * WIRED 2026-08-09: the MicoAir WiFi Link was bidirectional at 99.9 % with
 * downlink pinned to the wire, so the deliberate no-dispatch constraint was
 * flipped to "use it". USART3_IRQHandler now feeds the IDLE-coalesced bytes
 * through the same 0xCC 0xDD parser UART5 uses, so every dashboard command
 * (CMD 0x01..0x18) is reachable via either UART. 0xCC 0xDE subscribe requests
 * remain UART5-only: they have no reply DMA on USART3 and the parser rejects
 * them silently there.
 *
 * The frame/length counters stay exposed for livewatch. UA3RxFrameCnt is the
 * operator's real-time proof the radio's downlink path is alive when the link
 * is being driven in full duplex. */
volatile uint32_t UA3RxFrameCnt = 0U;   /* IDLE events that yielded >0 bytes */
volatile uint16_t UA3RxLastLen  = 0U;   /* byte count of the most recent burst */
 
void USART3_Configuration(void)
{

    USART_InitTypeDef usart3;
    GPIO_InitTypeDef  gpio;
    NVIC_InitTypeDef  nvic;
	  DMA_InitTypeDef   DMA_InitStructure;

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOC | RCC_AHB1Periph_DMA1,ENABLE);//ʹ��PA�˿�ʱ��
    RCC_APB1PeriphClockCmd(RCC_APB1Periph_USART3,ENABLE);//ʹ��USART2ʱ��
	

    GPIO_PinAFConfig(GPIOC,GPIO_PinSource10,GPIO_AF_USART3);
    GPIO_PinAFConfig(GPIOC,GPIO_PinSource11,GPIO_AF_USART3); 

	  gpio.GPIO_Pin = GPIO_Pin_10 | GPIO_Pin_11;
	  gpio.GPIO_Mode = GPIO_Mode_AF;//����ģʽ
    gpio.GPIO_OType = GPIO_OType_PP;//�������
    gpio.GPIO_Speed = GPIO_Speed_100MHz;//IO���ٶ�Ϊ100MHz
    gpio.GPIO_PuPd = GPIO_PuPd_NOPULL;
  	GPIO_Init(GPIOC,&gpio);//�����趨������ʼ��GPIOA

	/*USART2���տ����ж�*/
    nvic.NVIC_IRQChannel = USART3_IRQn;
    nvic.NVIC_IRQChannelPreemptionPriority = 5;//��ռ���ȼ�  (matches configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY -- below the FreeRTOS syscall ceiling, maskable by critical sections; see stack-hardening spec §3.4)
    nvic.NVIC_IRQChannelSubPriority = 1;//�����ȼ�
    nvic.NVIC_IRQChannelCmd = ENABLE;//IRQͨ��ʹ�� 
    NVIC_Init(&nvic);//����ָ���Ĳ�����ʼ��VIC�Ĵ���
	
	/*DMA��������ж�*/
	  nvic.NVIC_IRQChannel = DMA1_Stream3_IRQn;
    nvic.NVIC_IRQChannelPreemptionPriority = 0;//��ռ���ȼ�
    nvic.NVIC_IRQChannelSubPriority = 7;//�����ȼ�
    nvic.NVIC_IRQChannelCmd = ENABLE;//IRQͨ��ʹ�� 
    NVIC_Init(&nvic);//����ָ���Ĳ�����ʼ��VIC�Ĵ���
    
		USART_DeInit(USART3);
    usart3.USART_BaudRate = USART3_BAUD;//������
    usart3.USART_WordLength = USART_WordLength_8b;//�ֳ�Ϊ8λ���ݸ�ʽ
    usart3.USART_StopBits = USART_StopBits_1;//һ��ֹͣλ
    usart3.USART_Parity = USART_Parity_No;//����żУ��λ
    usart3.USART_Mode = USART_Mode_Tx|USART_Mode_Rx;//�շ�ģʽ
    usart3.USART_HardwareFlowControl = USART_HardwareFlowControl_None;//��Ӳ������������
    USART_Init(USART3,&usart3);//��ʼ������

    USART_ITConfig(USART3,USART_IT_IDLE,ENABLE);//ʹ�ܴ��ڿ����ж�
	  USART_DMACmd(USART3,USART_DMAReq_Rx,ENABLE);
	  USART_DMACmd(USART3,USART_DMAReq_Tx,ENABLE);
	
    USART_Cmd(USART3,ENABLE);//ʹ�ܴ���
	//Rx
	  DMA_DeInit(DMA1_Stream1);
    DMA_InitStructure.DMA_Channel            = DMA_Channel_4;//�����ַ
    DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(USART3->DR);//�ڴ��ַ
    DMA_InitStructure.DMA_Memory0BaseAddr    = NULL;
    DMA_InitStructure.DMA_DIR                = DMA_DIR_PeripheralToMemory;//DMA����Ϊ����
    DMA_InitStructure.DMA_BufferSize         = USART3_RXDMA_LEN;//����DMA�ڴ������ĳ���
    DMA_InitStructure.DMA_PeripheralInc      = DMA_PeripheralInc_Disable;
    DMA_InitStructure.DMA_MemoryInc          = DMA_MemoryInc_Enable;
    DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;
    DMA_InitStructure.DMA_MemoryDataSize     = DMA_MemoryDataSize_Byte;
    DMA_InitStructure.DMA_Mode               = DMA_Mode_Circular;
    DMA_InitStructure.DMA_Priority           = DMA_Priority_VeryHigh;
    DMA_InitStructure.DMA_FIFOMode           = DMA_FIFOMode_Disable;
    DMA_InitStructure.DMA_FIFOThreshold      = DMA_FIFOThreshold_Full;
    DMA_InitStructure.DMA_MemoryBurst        = DMA_MemoryBurst_Single;
    DMA_InitStructure.DMA_PeripheralBurst    = DMA_PeripheralBurst_Single;
    /* Overrides the Memory0BaseAddr = NULL set above. The RX stream had no
     * destination address AND was never enabled, so UA3RxDMAbuf was always
     * empty and every byte the long-range module sent was lost. Mirrors the
     * working UART5 RX setup (BSP/usart5.c:84,100). */
    DMA_InitStructure.DMA_Memory0BaseAddr    = (uint32_t)UA3RxDMAbuf;
    DMA_Init(DMA1_Stream1,&DMA_InitStructure);
    DMA_Cmd(DMA1_Stream1,ENABLE);

	//Tx

	  DMA_DeInit(DMA1_Stream3);
    DMA_InitStructure.DMA_Channel            = DMA_Channel_4;               //�����ַ
    DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(USART3->DR);
    DMA_InitStructure.DMA_Memory0BaseAddr    = NULL;
    DMA_InitStructure.DMA_DIR                = DMA_DIR_MemoryToPeripheral;  //DMA����Ϊ����
    DMA_InitStructure.DMA_BufferSize         = NULL;            //����DMA�ڴ������ĳ���
    DMA_InitStructure.DMA_PeripheralInc      = DMA_PeripheralInc_Disable;
    DMA_InitStructure.DMA_MemoryInc          = DMA_MemoryInc_Enable;
    DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;
    DMA_InitStructure.DMA_MemoryDataSize     = DMA_MemoryDataSize_Byte;
    DMA_InitStructure.DMA_Mode               = DMA_Mode_Normal;//
    DMA_InitStructure.DMA_Priority           = DMA_Priority_VeryHigh;
    DMA_InitStructure.DMA_FIFOMode           = DMA_FIFOMode_Disable;
    DMA_InitStructure.DMA_FIFOThreshold      = DMA_FIFOThreshold_Full;
    DMA_InitStructure.DMA_MemoryBurst        = DMA_MemoryBurst_Single;
    DMA_InitStructure.DMA_PeripheralBurst    = DMA_PeripheralBurst_Single;
    DMA_Init(DMA1_Stream3,&DMA_InitStructure);

    DMA_ITConfig(DMA1_Stream3,DMA_IT_TC,ENABLE);//�����ж�ʹ��
		
    DMA_Cmd(DMA1_Stream3,DISABLE);
}


//	//Rx
//	  DMA_DeInit(DMA1_Stream1);
//    DMA_InitStructure.DMA_Channel            = DMA_Channel_4;//�����ַ
//    DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(USART3->DR);//�ڴ��ַ
//    DMA_InitStructure.DMA_Memory0BaseAddr    = NULL;
//    DMA_InitStructure.DMA_DIR                = DMA_DIR_PeripheralToMemory;//DMA����Ϊ����
//    DMA_InitStructure.DMA_BufferSize         = NULL;//����DMA�ڴ������ĳ���
//    DMA_InitStructure.DMA_PeripheralInc      = DMA_PeripheralInc_Disable;
//    DMA_InitStructure.DMA_MemoryInc          = DMA_MemoryInc_Enable;
//    DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;
//    DMA_InitStructure.DMA_MemoryDataSize     = DMA_MemoryDataSize_Byte;
//    DMA_InitStructure.DMA_Mode               = DMA_Mode_Circular;
//    DMA_InitStructure.DMA_Priority           = DMA_Priority_VeryHigh;
//    DMA_InitStructure.DMA_FIFOMode           = DMA_FIFOMode_Disable;
//    DMA_InitStructure.DMA_FIFOThreshold      = DMA_FIFOThreshold_Full;
//    DMA_InitStructure.DMA_MemoryBurst        = DMA_MemoryBurst_Single;
//    DMA_InitStructure.DMA_PeripheralBurst    = DMA_PeripheralBurst_Single;
//    DMA_Init(DMA1_Stream1,&DMA_InitStructure);
//	
//	//Tx
//		DMA_InitTypeDef		dma;
//	  DMA_DeInit(DMA1_Stream3);
//    DMA_InitStructure.DMA_Channel            = DMA_Channel_4;               //�����ַ
//    DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(USART3->DR);
//    DMA_InitStructure.DMA_Memory0BaseAddr    = NULL;
//    DMA_InitStructure.DMA_DIR                = DMA_DIR_MemoryToPeripheral;  //DMA����Ϊ����
//    DMA_InitStructure.DMA_BufferSize         = NULL;            //����DMA�ڴ������ĳ���
//    DMA_InitStructure.DMA_PeripheralInc      = DMA_PeripheralInc_Disable;
//    DMA_InitStructure.DMA_MemoryInc          = DMA_MemoryInc_Enable;
//    DMA_InitStructure.DMA_PeripheralDataSize = DMA_PeripheralDataSize_Byte;
//    DMA_InitStructure.DMA_MemoryDataSize     = DMA_MemoryDataSize_Byte;
//    DMA_InitStructure.DMA_Mode               = DMA_Mode_Normal;
//    DMA_InitStructure.DMA_Priority           = DMA_Priority_VeryHigh;
//    DMA_InitStructure.DMA_FIFOMode           = DMA_FIFOMode_Disable;
//    DMA_InitStructure.DMA_FIFOThreshold      = DMA_FIFOThreshold_Full;
//    DMA_InitStructure.DMA_MemoryBurst        = DMA_MemoryBurst_Single;
//    DMA_InitStructure.DMA_PeripheralBurst    = DMA_PeripheralBurst_Single;
//    DMA_Init(DMA1_Stream3,&DMA_InitStructure);

//    //DMA_ITConfig(DMA1_Stream3,DMA_IT_TC,ENABLE);
//		DMA_Init(DMA1_Stream3, &dma);
//		//DMA_ITConfig(DMA1_Stream1,DMA_IT_TC,ENABLE);
//    DMA_Cmd(DMA1_Stream3,DISABLE);
//		//DMA_Cmd(DMA1_Stream1,ENABLE);//�Ƚ��ã��ò���



/* ---- 0x09 subscribe data stream + throughput frames ---------------------
 * CONTINUOUS TX RING on DMA1_Stream3, replacing a one-frame-per-tick hand-off.
 *
 * The old path armed the DMA straight from the caller's buffer and refused
 * ("skip if busy") whenever the previous transfer had not drained. That made
 * FRAME SIZE the limit rather than byte rate: measured 2026-08-09, a 684 B
 * frame (7.49 ms at 913043 baud) cleared the ~12.47 ms Send_Task tick and ran
 * at 80.4 Hz, but an 884 B frame (9.68 ms) straddled it, every other tick was
 * refused, and the cadence collapsed to 48.3 Hz -- while the radio itself lost
 * NOTHING (0.00 % at every rung, knee never reached). The wasted capacity was
 * entirely ours.
 *
 * Now producers copy into a ring and return; the DMA drains it back to back,
 * arming the next chunk from the transfer-complete IRQ. The line stays busy as
 * long as bytes are queued, so the link runs at the UART wire rate (91304 B/s)
 * instead of at whatever fits inside one tick.
 *
 * Single-producer/single-consumer: only task context (Send_Task, via
 * usart3_send() and Subscribe_StreamTick()) advances head, only the DMA IRQ
 * advances tail. The two short critical sections below exist because tx_arm()
 * is reachable from both.
 *
 * A frame is queued WHOLE or not at all. Splitting one across a wrap is fine
 * (the ring is contiguous to the consumer), but writing a partial frame when
 * the ring is full is not: the host would see a torn record and score it as
 * corruption instead of as loss. */
static uint8_t          s_tx_ring[USART3_TX_RING_LEN];
static volatile uint16_t s_tx_head   = 0U;   /* producer: next byte to write   */
static volatile uint16_t s_tx_tail   = 0U;   /* consumer: next byte to send    */
static volatile uint16_t s_tx_chunk  = 0U;   /* bytes in the live transfer     */
static volatile uint8_t  s_tx_active = 0U;   /* 1 while DMA1_Stream3 is armed  */

volatile uint32_t UA3TxFrames = 0U;
volatile uint32_t UA3TxDrops  = 0U;
volatile uint16_t UA3TxPeak   = 0U;

static uint16_t tx_used(void)
{
    uint16_t h = s_tx_head;
    uint16_t t = s_tx_tail;
    return (uint16_t)((h >= t) ? (h - t) : (USART3_TX_RING_LEN - t + h));
}

/* Arm the next contiguous run, tail -> (head or the physical end of the ring).
 * Caller must hold exclusive access: task context masks interrupts, the IRQ
 * already has it. No-op when a transfer is in flight or the ring is empty.
 *
 * Stopping at the ring's end costs nothing: the USART's own shift register
 * keeps the line busy for a full character (10.9 us at 913043 baud), which is
 * far longer than the IRQ takes to arm the wrapped remainder. */
static void tx_arm(void)
{
    uint16_t h;
    uint16_t t;
    uint16_t n;

    if (s_tx_active != 0U)
    {
        return;
    }
    h = s_tx_head;
    t = s_tx_tail;
    if (h == t)
    {
        return;                       /* ring empty: line goes idle until the next frame */
    }
    n = (uint16_t)((h > t) ? (h - t) : (USART3_TX_RING_LEN - t));

    /* RM0090: every event flag must be cleared before EN is set again, not just
     * TCIF3 -- a latched HTIF3/FEIF3 otherwise blocks the stream permanently. */
    DMA_ClearFlag(DMA1_Stream3, DMA_FLAG_TCIF3 | DMA_FLAG_HTIF3 | DMA_FLAG_TEIF3
                              | DMA_FLAG_DMEIF3 | DMA_FLAG_FEIF3);
    DMA1_Stream3->M0AR = (uint32_t)&s_tx_ring[t];
    DMA1_Stream3->NDTR = n;
    s_tx_chunk  = n;
    s_tx_active = 1U;
    DMA_Cmd(DMA1_Stream3, ENABLE);
}

void Usart3_Tx_DmaIsr(void)
{
    if (DMA_GetITStatus(DMA1_Stream3, DMA_IT_TCIF3) == RESET)
    {
        return;
    }
    DMA_ClearFlag(DMA1_Stream3, DMA_FLAG_TCIF3 | DMA_FLAG_HTIF3 | DMA_FLAG_TEIF3
                              | DMA_FLAG_DMEIF3 | DMA_FLAG_FEIF3);
    DMA_Cmd(DMA1_Stream3, DISABLE);   /* Normal mode already cleared EN; explicit for clarity */

    s_tx_tail   = (uint16_t)((s_tx_tail + s_tx_chunk) % USART3_TX_RING_LEN);
    s_tx_chunk  = 0U;
    s_tx_active = 0U;
    tx_arm();                         /* back to back: this is what keeps the wire saturated */
}

uint8_t Usart3_Stream_Busy(void)
{
    return (tx_used() > (uint16_t)(USART3_TX_RING_LEN / 2U)) ? 1U : 0U;
}

uint8_t Usart3_Stream_TxSend(const uint8_t* buf, uint16_t len)
{
    uint32_t pri;
    uint16_t h;
    uint16_t i;

    if ((buf == 0) || (len == 0U))
    {
        return 0U;
    }
    if (len >= USART3_TX_RING_LEN)
    {
        UA3TxDrops++;                 /* cannot ever fit; caller must shrink the frame */
        return 0U;
    }

    pri = __get_PRIMASK();
    __disable_irq();
    if ((uint16_t)(USART3_TX_RING_LEN - 1U - tx_used()) < len)
    {
        __set_PRIMASK(pri);
        UA3TxDrops++;                 /* whole frame refused; never a torn one */
        return 0U;
    }
    h = s_tx_head;
    __set_PRIMASK(pri);

    /* Copied outside the critical section: only this context advances head, so
     * the region we are filling is invisible to the DMA until head moves. */
    for (i = 0U; i < len; i++)
    {
        s_tx_ring[h] = buf[i];
        h = (uint16_t)(((uint16_t)(h + 1U) == USART3_TX_RING_LEN) ? 0U : (uint16_t)(h + 1U));
    }

    pri = __get_PRIMASK();
    __disable_irq();
    s_tx_head = h;
    {
        uint16_t used = tx_used();
        if (used > UA3TxPeak)
        {
            UA3TxPeak = used;         /* headroom check: near LEN means we are at the wire */
        }
    }
    tx_arm();                         /* no-op if the DMA is already streaming */
    __set_PRIMASK(pri);

    UA3TxFrames++;
    return 1U;
}
