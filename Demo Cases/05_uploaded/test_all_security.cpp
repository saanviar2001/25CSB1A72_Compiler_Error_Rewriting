#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>

void trigger_gets_overflow() {
    char buf[64];
    gets(buf);
}

void trigger_unsafe_string_functions(const char* user_input) {
    char dest[64];
    strcpy(dest, user_input);
    strcat(dest, "_suffix");
    char out[128];
    sprintf(out, "%s", dest);
}

void trigger_unbounded_scanf() {
    char username[32];
    scanf("%s", username);
}

void trigger_command_injection(const char* cmd) {
    system(cmd);
}

void trigger_weak_randomness() {
    int secret = rand();
    int token  = rand();
    int key    = rand();
    (void)secret; (void)token; (void)key;
}

void trigger_double_free() {
    int* p = new int(42);
    delete p;
    delete p;
}

void trigger_use_after_free() {
    int* q = new int(99);
    delete q;
    *q = 7;
}

void trigger_integer_overflow(int count) {
    size_t alloc_size = count * 1024;
    void* mem = malloc(alloc_size);
    (void)mem;
}

void trigger_unsafe_memcpy(void* dst, const void* src) {
    memcpy(dst, src, 256);
}

void trigger_pointer_arithmetic(char* datap) {
    char* cursor = datap;
    cursor++;
    *cursor = 'X';
}

void trigger_memory_leak() {
    char* bufp = (char*)malloc(128);
    bufp[0] = 'A';
}

void trigger_assert(int value) {
    assert(value > 0);
}

void trigger_security_debt() {
    char password[64];
    strcpy(password, "hunter2");
}

void trigger_unused_variable() {
    int unused_flag = 1;
    printf("no-op\n");
    (void)unused_flag;
}

int trigger_uninitialized() {
    int x;
    return x;
}

void trigger_format_string(char* user_fmt) {
    printf(user_fmt);
}

void trigger_sign_mismatch(int n) {
    unsigned int u = 10;
    if (n < u) {
        printf("less\n");
    }
}

int main() {
    char dummy[256] = {0};
    trigger_gets_overflow();
    trigger_unsafe_string_functions("hello");
    trigger_unbounded_scanf();
    trigger_command_injection("ls");
    trigger_weak_randomness();
    trigger_double_free();
    trigger_use_after_free();
    trigger_integer_overflow(100);
    trigger_unsafe_memcpy(dummy, dummy);
    trigger_pointer_arithmetic(dummy);
    trigger_memory_leak();
    trigger_assert(1);
    trigger_security_debt();
    trigger_unused_variable();
    trigger_uninitialized();
    trigger_format_string(dummy);
    trigger_sign_mismatch(-1);
    return 0;
}
