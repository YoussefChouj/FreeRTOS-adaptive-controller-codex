#include "usart4.h"

_linux_data_st linux_data;
yolo_data linux_yolo_data;
//PA0  TX
//PA1  RX
UCHAR8 UA4RxDMAbuf[UART4_RXDMA_LEN] = {0};
UCHAR8 UA4RxMailbox[UART4_RXMB_LEN] = {0};
USART_RX_TypeDef UART4_Rcr = {UART4,UART4_RX_STREAM,UA4RxMailbox,UA4RxDMAbuf,UART4_RXMB_LEN,UART4_RXDMA_LEN,0,0,0};
void UART4_Configuration(void)  
{
	USART_InitTypeDef uart4;
	GPIO_InitTypeDef  gpio;
	NVIC_InitTypeDef  nvic;
	DMA_InitTypeDef   DMA_InitStructure;

	RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA | RCC_AHB1Periph_DMA1,ENABLE);//ʹ��PC�˿�ʱ��
	RCC_APB1PeriphClockCmd(RCC_APB1Periph_UART4,ENABLE);//ʹ��UART4ʱ��

	GPIO_PinAFConfig(GPIOA,GPIO_PinSource0,GPIO_AF_UART4); 
	GPIO_PinAFConfig(GPIOA,GPIO_PinSource1,GPIO_AF_UART4); 

	gpio.GPIO_Pin = GPIO_Pin_0|GPIO_Pin_1;
	gpio.GPIO_Mode = GPIO_Mode_AF;//����ģʽ
	gpio.GPIO_OType = GPIO_OType_PP;//�������
	gpio.GPIO_Speed = GPIO_Speed_100MHz;//IO���ٶ�Ϊ50MHz
	gpio.GPIO_PuPd = GPIO_PuPd_UP;//����
	GPIO_Init(GPIOA,&gpio);//�����趨������ʼ��GPIOC

	nvic.NVIC_IRQChannel = UART4_IRQn;
	nvic.NVIC_IRQChannelPreemptionPriority = 0;//��ռ���ȼ�
	nvic.NVIC_IRQChannelSubPriority = 0;//�����ȼ�
	nvic.NVIC_IRQChannelCmd = ENABLE;//IRQͨ��ʹ�� 
	NVIC_Init(&nvic);//����ָ���Ĳ�����ʼ��VIC�Ĵ���

	uart4.USART_BaudRate = 115200;//������
	uart4.USART_WordLength = USART_WordLength_8b;//�ֳ�Ϊ8λ���ݸ�ʽ
	uart4.USART_StopBits = USART_StopBits_1;//һ��ֹͣλ
	uart4.USART_Parity = USART_Parity_No;//����żУ��λ
	uart4.USART_Mode = USART_Mode_Rx|USART_Mode_Tx;//������
	uart4.USART_HardwareFlowControl = USART_HardwareFlowControl_None;//��Ӳ������������
	USART_Init(UART4,&uart4);//��ʼ������

	USART_DMACmd(UART4,USART_DMAReq_Rx,ENABLE);
	USART_DMACmd(UART4,USART_DMAReq_Tx,ENABLE);
	USART_ITConfig(UART4,USART_IT_IDLE,ENABLE); //���������ж�

	USART_Cmd(UART4,ENABLE);//ʹ�ܴ���

	DMA_DeInit(DMA1_Stream2);
	DMA_InitStructure.DMA_Channel= DMA_Channel_4;//ͨ��
	DMA_InitStructure.DMA_PeripheralBaseAddr = (uint32_t)&(UART4->DR);//�����ַ
	DMA_InitStructure.DMA_Memory0BaseAddr = (uint32_t)UA4RxDMAbuf;//������4���յ�������ucRxData_DMA1_Stream2[]��ڴ����ַ
	DMA_InitStructure.DMA_DIR = DMA_DIR_PeripheralToMemory;//�������ݴ��䷽��
	DMA_InitStructure.DMA_BufferSize = UART4_RXDMA_LEN;//����DMAһ�δ����������Ĵ�С
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
	DMA_Init(DMA1_Stream2,&DMA_InitStructure);

	//DMA_ITConfig(DMA1_Stream2,DMA_IT_TC,ENABLE);
	DMA_Cmd(DMA1_Stream2,ENABLE);

	/////////////////////////TX
	DMA_InitTypeDef		dma;
	DMA_DeInit(DMA1_Stream4);
	while( DMA_GetCmdStatus(DMA1_Stream4) == ENABLE );			//�ȴ�DMA������

	dma.DMA_Channel				=	DMA_Channel_4;
	dma.DMA_PeripheralBaseAddr	=	(uint32_t)&(UART4->DR);
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
	DMA_Init(DMA1_Stream4, &dma);
	DMA_Cmd(DMA1_Stream4, DISABLE);
}

typedef struct { uint8_t id; uint8_t index; float value; } GS_Cmd_t;
volatile GS_Cmd_t gs_cmd_queue[16];
volatile uint8_t gs_cmd_head = 0, gs_cmd_tail = 0;
volatile uint32_t gs_cmd_drop_count = 0;

void Handle_UART4_GroundStation_Command(void)
{
	// CONSTRAINT: Frame layout and XOR CRC must match serial_bridge.py _pack_command_frame().
	// ARCH: Queue storage ownership is here; usart5.c is an additional ingress source.
	// Format: [0xCC] [0xDD] [CMD_ID: uint8] [INDEX: uint8] [VALUE: float32 LE] [CRC8]
	// Total frame length is 9 bytes.
	// WHY loop: the PC serial bridge coalesces multiple rapid writes into one OS-level burst.
	// IDLE fires once for the whole burst; parsing only mailbox[0..8] silently drops every
	// frame after the first (e.g. the execute flag sent last in a TWC sequence).
	uint16_t total = UART4_Rcr.rxSize;
	uint16_t offset = 0;
	while (offset + 9U <= total)
	{
		if (UA4RxMailbox[offset] == 0xCC && UA4RxMailbox[offset + 1U] == 0xDD)
		{
			uint8_t cmd_id = UA4RxMailbox[offset + 2U];
			uint8_t index  = UA4RxMailbox[offset + 3U];

			union {
				float f;
				uint8_t b[4];
			} val;

			val.b[0] = UA4RxMailbox[offset + 4U];
			val.b[1] = UA4RxMailbox[offset + 5U];
			val.b[2] = UA4RxMailbox[offset + 6U];
			val.b[3] = UA4RxMailbox[offset + 7U];

			uint8_t crc = UA4RxMailbox[offset + 8U];
			uint8_t calc_crc = 0;
			int i;
			for (i = 2; i < 8; i++) {
				calc_crc ^= UA4RxMailbox[offset + (uint16_t)i];
			}

			if (calc_crc == crc) {
				uint8_t next_head = (uint8_t)((gs_cmd_head + 1U) % 16U);
				if (next_head != gs_cmd_tail) {
					gs_cmd_queue[gs_cmd_head].id    = cmd_id;
					gs_cmd_queue[gs_cmd_head].index = index;
					gs_cmd_queue[gs_cmd_head].value = val.f;
					gs_cmd_head = next_head;
				} else {
					gs_cmd_drop_count++;
				}
			}
			offset += 9U;
		}
		else
		{
			offset += 1U;
		}
	}
}
