#ifndef __IOUSART_H_
#define	__IOUSART_H_
 
#include "main.h"
 

//(1/9600) = 104us  1/38400 =26
#define IO_USART_SENDDELAY_TIME 	 26	
 
enum{
	COM_START_BIT,
	COM_D0_BIT,
	COM_D1_BIT,
	COM_D2_BIT,
	COM_D3_BIT,
	COM_D4_BIT,
	COM_D5_BIT,
	COM_D6_BIT,
	COM_D7_BIT,
	COM_STOP_BIT,
};
 
 
void iouart1_SendByte(uint8_t uByte);
//void iouart1_init(void);
 
#endif /* __LED_H */