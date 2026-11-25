#include <cstdio>
#include <cstdlib>
#include <cstring>

void crit_gets() {
    char buf[64];
    gets(buf);
}

void high_system(const char* c) {
    system(c);
}

void high_popen(const char* c) {
    FILE* f = popen(c, "r");
    if (f) pclose(f);
}

void high_asm() {
    __asm__("nop");
}

void high_format(char* f) {
    printf(f);
}

void high_strcpy(const char* s) {
    char d[64];
    strcpy(d, s);
}

int main() {
    crit_gets();
    high_system("ls");
    high_popen("whoami");
    high_asm();
    char buf[100];
    high_format(buf);
    high_strcpy("x");
    return 0;
}
