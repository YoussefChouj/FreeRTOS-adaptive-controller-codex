//spi_rx_buffer[1] = 0x0A  // Binary: 0b0000'1010
//<< 8 positions           // Binary: 0b0000'1010'0000'0000 (now 0x0A00)

//spi_rx_buffer[0] = 0xBC  // Binary: 0b1011'1100

//Result (|):        0x0ABC // Binary: 0b0000'1010'1011'1100c

#include <stdint.h>
#include <stdio.h>

#define SPI_RX_BUFFER_SIZE 2

void Bit_shifting_example(void);

int main(void)
{
    //Bit_shifting_example();
    printf("Sise of uint8_t: %zu bytes\n", sizeof(uint8_t));
    printf("Size of uint16_t: %zu bytes\n", sizeof(uint16_t));
    printf("Size of float: %zu bytes\n", sizeof(float));

    return 0;
}



void Bit_shifting_example(void)
{
    uint8_t spi_rx_buffer[SPI_RX_BUFFER_SIZE];
    uint16_t combined_value;
    int i;
    // Example values for demonstration
    spi_rx_buffer[1] = 0b00001010; // Upper byte
    spi_rx_buffer[0] = 0b10111100; // Lower byte
    printf("spi_rx_buffer[1] = 0b");
    for (i = 7; i >= 0; i--) {
        printf("%d", (spi_rx_buffer[1] >> i) & 1);
    }
    printf("\n");   
    printf("spi_rx_buffer[0] = 0b");
    for (i = 7; i >= 0; i--) {
        printf("%d", (spi_rx_buffer[0] >> i) & 1);
    }
    printf("\n");
    combined_value = ((uint16_t)spi_rx_buffer[1] << 8) ;
    for (i= 15; i >= 0; i--){
        printf("%d", (combined_value >> i) & 1);
    }
}

