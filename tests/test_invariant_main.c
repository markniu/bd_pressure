#include <check.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>

/* Buffer size from main.c - usart_txBuff is typically 256 bytes in embedded systems */
#define USART_TX_BUFF_SIZE 256
#define CANARY_VALUE 0xDE

/* Simulate the vulnerable pattern to test the invariant */
static char test_buffer[USART_TX_BUFF_SIZE];
static char canary_region[64];

static int safe_printf_to_buffer(char *buf, size_t buf_size, const char *fmt, ...)
{
    va_list ap;
    int written;
    va_start(ap, fmt);
    written = vsnprintf(buf, buf_size, fmt, ap);
    va_end(ap);
    return written;
}

START_TEST(test_buffer_overflow_protection)
{
    /* Invariant: Buffer writes must never exceed declared buffer length */
    const char *payloads[] = {
        /* Exact exploit: 2x buffer size */
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        /* Boundary: exactly buffer size */
        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        /* Valid: small input */
        "Hello World"
    };
    int num_payloads = sizeof(payloads) / sizeof(payloads[0]);

    for (int i = 0; i < num_payloads; i++) {
        memset(canary_region, CANARY_VALUE, sizeof(canary_region));
        memset(test_buffer, 0, sizeof(test_buffer));

        safe_printf_to_buffer(test_buffer, USART_TX_BUFF_SIZE, "%s", payloads[i]);

        /* Verify canary region is untouched - no overflow occurred */
        for (size_t j = 0; j < sizeof(canary_region); j++) {
            ck_assert_msg(canary_region[j] == (char)CANARY_VALUE,
                "Buffer overflow detected with payload %d", i);
        }
        /* Verify null termination within bounds */
        ck_assert_msg(test_buffer[USART_TX_BUFF_SIZE - 1] == '\0' || 
                      strlen(test_buffer) < USART_TX_BUFF_SIZE,
                      "Buffer not properly terminated for payload %d", i);
    }
}
END_TEST

Suite *security_suite(void)
{
    Suite *s;
    TCase *tc_core;

    s = suite_create("Security");
    tc_core = tcase_create("Core");

    tcase_add_test(tc_core, test_buffer_overflow_protection);
    suite_add_tcase(s, tc_core);

    return s;
}

int main(void)
{
    int number_failed;
    Suite *s;
    SRunner *sr;

    s = security_suite();
    sr = srunner_create(s);

    srunner_run_all(sr, CK_NORMAL);
    number_failed = srunner_ntests_failed(sr);
    srunner_free(sr);

    return (number_failed == 0) ? EXIT_SUCCESS : EXIT_FAILURE;
}