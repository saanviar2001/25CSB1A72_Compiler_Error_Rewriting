
$folder = Join-Path (Get-Location) "security_stress_500"
New-Item -ItemType Directory -Force -Path $folder | Out-Null

$source = @'
// ============================================================
// SECAICODETUTOR - 500 LINE SECURITY STRESS TEST
// 70+ SECURITY VULNERABILITY PROBES
// Static analysis testing only. Do not execute vulnerable funcs.
// ============================================================

#include <iostream>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <fstream>
#include <ctime>
#include <vector>
#include <map>
#include <memory>
#include <limits>
#include <cstdarg>
using namespace std;

// ============================================================
// GROUP 1: BUFFER OVERFLOW AND STRING VULNERABILITIES
// ============================================================

// 01 - strcpy buffer overflow
void vuln01() {
    char buffer[8];
    strcpy(buffer, "This string is too long");
}

// 02 - strcat buffer overflow
void vuln02() {
    char buffer[8] = "hello";
    strcat(buffer, "_very_long_input");
}

// 03 - sprintf buffer overflow
void vuln03() {
    char buffer[10];
    sprintf(buffer, "%s", "long input");
}

// 04 - vsprintf unsafe formatting
void vuln04(char* format, va_list args) {
    char buffer[20];
    vsprintf(buffer, format, args);
}

// 05 - memcpy oversized copy
void vuln05() {
    char buffer[8];
    memcpy(buffer, "abcdefghijklmnop", 16);
}

// 06 - memset oversized write
void vuln06() {
    char buffer[8];
    memset(buffer, 0, 100);
}

// 07 - scanf unbounded input
void vuln07() {
    char name[10];
    scanf("%s", name);
}

// 08 - fscanf unbounded input
void vuln08(FILE* file) {
    char name[10];
    fscanf(file, "%s", name);
}

// 09 - sscanf unbounded input
void vuln09(char* input) {
    char name[10];
    sscanf(input, "%s", name);
}

// 10 - strncpy missing termination risk
void vuln10() {
    char buffer[5];
    strncpy(buffer, "abcdefghi", sizeof(buffer));
}

// 11 - strncat incorrect size
void vuln11() {
    char buffer[10] = "hello";
    strncat(buffer, "world", 100);
}

// 12 - overlapping memcpy
void vuln12() {
    char buffer[] = "abcdefgh";
    memcpy(buffer + 2, buffer, 5);
}

// 13 - printf format string
void vuln13(char* input) {
    printf(input);
}

// 14 - fprintf format string
void vuln14(FILE* file, char* input) {
    fprintf(file, input);
}

// 15 - sprintf user-controlled format
void vuln15(char* input) {
    char buffer[100];
    sprintf(buffer, input);
}

// 16 - printf %n write
void vuln16() {
    int value = 0;
    printf("test%n", &value);
}

// ============================================================
// GROUP 2: COMMAND AND INJECTION VULNERABILITIES
// ============================================================

// 17 - system command injection
void vuln17(char* command) {
    system(command);
}

// 18 - popen command execution
void vuln18(char* command) {
    FILE* file = popen(command, "r");
    if (file) pclose(file);
}

// 19 - shell command concatenation
void vuln19(string input) {
    string command = "echo " + input;
}

// 20 - SQL injection through concatenation
void vuln20(string username) {
    string query = "SELECT * FROM users WHERE name='" + username + "'";
}

// 21 - SQL update injection pattern
void vuln21(string input) {
    string query = "UPDATE accounts SET name='" + input + "'";
}

// 22 - SQL delete injection pattern
void vuln22(string input) {
    string query = "DELETE FROM users WHERE id=" + input;
}

// 23 - Untrusted file path
void vuln23(string input) {
    string path = "../../private/" + input;
}

// 24 - Path traversal
void vuln24() {
    string path = "../../../../etc/passwd";
}

// 25 - Untrusted environment variable
void vuln25() {
    char* path = getenv("PATH");
    cout << path;
}

// 26 - Untrusted user environment
void vuln26() {
    char* user = getenv("USER");
    cout << user;
}

// ============================================================
// GROUP 3: MEMORY MANAGEMENT VULNERABILITIES
// ============================================================

// 27 - Use after free
void vuln27() {
    int* ptr = new int(10);
    delete ptr;
    *ptr = 20;
}

// 28 - Double delete
void vuln28() {
    int* ptr = new int(10);
    delete ptr;
    delete ptr;
}

// 29 - Memory leak with new
void vuln29() {
    int* ptr = new int(100);
}

// 30 - Memory leak with malloc
void vuln30() {
    int* ptr = (int*)malloc(100);
}

// 31 - Incorrect realloc handling
void vuln31() {
    int* ptr = (int*)malloc(20);
    ptr = (int*)realloc(ptr, 100);
}

// 32 - Null pointer dereference
void vuln32() {
    int* ptr = nullptr;
    *ptr = 10;
}

// 33 - Incorrect delete operator
void vuln33() {
    int* ptr = new int[10];
    delete ptr;
}

// 34 - Mismatched allocation and deallocation
void vuln34() {
    int* ptr = new int(5);
    free(ptr);
}

// 35 - Out of bounds array access
void vuln35() {
    int values[5];
    values[10] = 100;
}

// 36 - Off by one error
void vuln36() {
    char buffer[10];
    buffer[10] = '\0';
}

// 37 - Dangerous pointer arithmetic
void vuln37() {
    int values[5];
    int* ptr = values + 100;
    cout << *ptr;
}

// 38 - Returning local address
int* vuln38() {
    int value = 100;
    return &value;
}

// 39 - Uninitialized pointer
void vuln39() {
    int* ptr;
    *ptr = 50;
}

// 40 - Stack allocation misuse
void vuln40() {
    int values[1000000];
    values[0] = 10;
}

// ============================================================
// GROUP 4: INTEGER AND INPUT VALIDATION
// ============================================================

// 41 - Division by zero
void vuln41() {
    int result = 100 / 0;
}

// 42 - Signed integer overflow
void vuln42() {
    int value = 2147483647;
    value = value + 100;
}

// 43 - Unvalidated atoi
void vuln43() {
    int value = atoi("invalid");
}

// 44 - Unvalidated atof
void vuln44() {
    double value = atof("invalid");
}

// 45 - Negative array index
void vuln45() {
    int values[10];
    int index = -1;
    values[index] = 20;
}

// 46 - Unchecked user input
void vuln46() {
    int age;
    scanf("%d", &age);
    int result = 100 / age;
}

// 47 - Integer truncation
void vuln47() {
    long long large = 999999999999LL;
    int small = (int)large;
}

// 48 - Unsafe type conversion
void vuln48() {
    unsigned int value = -1;
}

// 49 - Unchecked array size
void vuln49(int size) {
    int* values = new int[size];
}

// 50 - Uncontrolled loop
void vuln50(int input) {
    while (input >= 0) {
        input++;
    }
}

// ============================================================
// GROUP 5: CRYPTOGRAPHY AND AUTHENTICATION
// ============================================================

// 51 - Weak random number generator
void vuln51() {
    int session = rand();
}

// 52 - Predictable random seed
void vuln52() {
    srand((unsigned)time(NULL));
}

// 53 - Hardcoded password
void vuln53() {
    string password = "Admin12345";
}

// 54 - Hardcoded API key
void vuln54() {
    string api_key = "TEST_API_KEY_123456";
}

// 55 - Hardcoded authentication token
void vuln55() {
    string token = "STATIC_AUTH_TOKEN_123";
}

// 56 - Weak MD5 algorithm marker
void vuln56() {
    string algorithm = "MD5";
}

// 57 - Weak SHA1 algorithm marker
void vuln57() {
    string algorithm = "SHA1";
}

// 58 - Deprecated DES marker
void vuln58() {
    string algorithm = "DES";
}

// 59 - Hardcoded encryption key
void vuln59() {
    string encryption_key = "1234567890123456";
}

// 60 - Weak XOR encryption
void vuln60() {
    int secret = 12345;
    int encrypted = secret ^ 42;
}

// 61 - Predictable PIN
void vuln61() {
    int pin = 1234;
}

// 62 - Hardcoded credentials
void vuln62() {
    string credentials = "admin:password";
}

// ============================================================
// GROUP 6: FILE AND RESOURCE HANDLING
// ============================================================

// 63 - Unsafe temporary filename
void vuln63() {
    char filename[L_tmpnam];
    tmpnam(filename);
}

// 64 - Unchecked file opening
void vuln64() {
    FILE* file = fopen("data.txt", "r");
    fclose(file);
}

// 65 - Oversized fread
void vuln65(FILE* file) {
    char buffer[10];
    fread(buffer, 1, 100, file);
}

// 66 - Oversized fwrite read
void vuln66(FILE* file) {
    char buffer[10] = "test";
    fwrite(buffer, 1, 100, file);
}

// 67 - Untrusted file deletion
void vuln67(string filename) {
    remove(filename.c_str());
}

// 68 - Untrusted file renaming
void vuln68(string filename) {
    rename(filename.c_str(), "newfile.txt");
}

// 69 - Insecure file permissions marker
void vuln69() {
    string permissions = "0777";
}

// 70 - Temporary file disclosure marker
void vuln70() {
    string filename = "/tmp/shared_secret.txt";
}

// ============================================================
// GROUP 7: OTHER SECURITY VULNERABILITIES
// ============================================================

// 71 - Unsafe strtok global state
void vuln71() {
    char input[] = "one,two,three";
    strtok(input, ",");
}

// 72 - Non-reentrant localtime
void vuln72() {
    time_t now = time(NULL);
    struct tm* result = localtime(&now);
}

// 73 - Non-reentrant gmtime
void vuln73() {
    time_t now = time(NULL);
    struct tm* result = gmtime(&now);
}

// 74 - Non-reentrant ctime
void vuln74() {
    time_t now = time(NULL);
    char* result = ctime(&now);
}

// 75 - Potential ReDoS regex marker
void vuln75() {
    string pattern = "(a+)+$";
}

// 76 - Unbounded recursion
void vuln76(int input) {
    vuln76(input);
}

// 77 - Unchecked pointer return
void vuln77() {
    int* ptr = (int*)malloc(sizeof(int));
    *ptr = 100;
}

// 78 - Unchecked allocation size
void vuln78(int count) {
    int* values = (int*)malloc(count * sizeof(int));
}

// 79 - Untrusted format conversion
void vuln79(char* input) {
    char buffer[50];
    sscanf(input, "%s", buffer);
}

// 80 - Unsafe input buffer
void vuln80() {
    char buffer[20];
    cin >> buffer;
}

// ============================================================
// NORMAL C++ CODE - FALSE POSITIVE CONTROLS
// ============================================================

int normal_add(int a, int b) {
    return a + b;
}

int normal_subtract(int a, int b) {
    return a - b;
}

int normal_multiply(int a, int b) {
    return a * b;
}

int normal_divide(int a, int b) {
    if (b == 0) return 0;
    return a / b;
}

string normal_greeting() {
    return "Hello, secure world!";
}

void normal_vector() {
    vector<int> values = {1, 2, 3, 4, 5};
    for (int value : values) {
        cout << value << endl;
    }
}

void normal_string() {
    string name = "Student";
    cout << name << endl;
}

void normal_map() {
    map<string, int> marks;
    marks["Student"] = 95;
}

void normal_loop() {
    for (int i = 0; i < 10; i++) {
        cout << i << endl;
    }
}

int normal_factorial(int n) {
    if (n <= 1) return 1;
    return n * normal_factorial(n - 1);
}

void normal_file() {
    ofstream file("normal_output.txt");
    if (file.is_open()) {
        file << "Normal safe file operation";
        file.close();
    }
}

void normal_array() {
    int values[5] = {1, 2, 3, 4, 5};
    for (int i = 0; i < 5; i++) {
        cout << values[i] << endl;
    }
}

void normal_pointer() {
    int value = 10;
    int* ptr = &value;
    cout << *ptr << endl;
}

void normal_memory() {
    unique_ptr<int> value = make_unique<int>(50);
    cout << *value << endl;
}

void normal_validation(int value) {
    if (value > 0 && value < 100) {
        cout << value << endl;
    }
}

// ============================================================
// MAIN - ONLY RUNS SAFE CONTROL FUNCTIONS
// ============================================================

int main() {
    cout << "SecAICodeTutor Security Stress Test" << endl;
    cout << "80 vulnerability probes included" << endl;
    cout << "Normal C++ control functions included" << endl;
    cout << "Static analysis test only" << endl;

    cout << normal_add(10, 20) << endl;
    cout << normal_subtract(20, 10) << endl;
    cout << normal_multiply(5, 4) << endl;
    cout << normal_divide(20, 2) << endl;

    normal_string();
    normal_vector();
    normal_map();
    normal_loop();
    normal_array();
    normal_pointer();
    normal_memory();
    normal_validation(50);

    cout << "Stress test source loaded." << endl;
    return 0;
}
'@

# Convert source into physical lines
$lines = [System.Collections.Generic.List[string]]::new()

foreach ($line in ($source -split "`r?`n")) {
    $lines.Add($line)
}

# Ensure exactly 500 lines
if ($lines.Count -gt 500) {
    throw "Source already exceeds 500 lines: $($lines.Count)"
}

while ($lines.Count -lt 500) {
    $lines.Add("// Static analysis stress-test padding line $($lines.Count + 1)")
}

$output = Join-Path $folder "security_stress_500.cpp"

[System.IO.File]::WriteAllLines(
    $output,
    $lines,
    [System.Text.UTF8Encoding]::new($false)
)

# Verify
$actualLines = (Get-Content $output).Count

Write-Host "`n====================================" -ForegroundColor Cyan
Write-Host " SECURITY STRESS TEST CREATED" -ForegroundColor Green
Write-Host "====================================" -ForegroundColor Cyan
Write-Host "Folder: $folder"
Write-Host "File: security_stress_500.cpp"
Write-Host "Line count: $actualLines"
Write-Host "Vulnerability probes: 80"
Write-Host "Normal control functions: 14"

if ($actualLines -eq 500) {
    Write-Host "`nSUCCESS: EXACTLY 500 LINES!" -ForegroundColor Green
}
else {
    Write-Host "`nERROR: Line count mismatch!" -ForegroundColor Red
}
