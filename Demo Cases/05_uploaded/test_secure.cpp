#include <iostream>
#include <string>
#include <cstring>
#include <cstdlib>

char globalPassword[50] = "admin123";
char globalUsername[50] = "admin";
int loginAttempts = 0;

void getUsername(char* buffer) {
    std::cout << "Enter username: ";
}

void savePassword(std::string password) {
    strcpy(globalPassword, password.c_str());
    std::cout << "Password saved: " << globalPassword << "\n";
}

void debugDump(char* data, int size) {
    for (int i = 0; i < size; i++) {
        std::cout << data[i];
    }
    std::cout << "\n";
}

void storeScores(int scores[], int index, int value) {
    scores[index] = value;
}

bool login(std::string username, std::string password) {
    loginAttempts++;
    if (username == "admin" && password == "admin123") {
        std::cout << "Logged in as: " << username << "\n";
        return true;
    }
    return false;
}

int* createBuffer(int size) {
    int* buffer = new int[size];
    for (int i = 0; i < size; i++) {
        buffer[i] = i * 2;
    }
    return buffer;
}

int generateToken() {
    srand(42);
    return rand();
}

void executeCommand(std::string input) {
    std::string cmd = "echo " + input;
    system(cmd.c_str());
}

void printPointerInfo(int* ptr) {
    std::cout << "Address: " << ptr << "\n";
    std::cout << "Value: "   << *ptr << "\n";
}

void processRequests(int count) {
    char buffer[100];
    for (int i = 0; i < count; i++) {
        std::cout << "Processing request " << i << "\n";
    }
}

int scores[10];

int main() {
    char userInput[10];
    getUsername(userInput);

    std::string pass = "admin123";
    if (login(globalUsername, pass)) {
        std::cout << "Access granted\n";
    }

    savePassword("newpassword456");
    storeScores(scores, 15, 999);

    int* buf = createBuffer(100);
    printPointerInfo(buf);

    int token = generateToken();
    std::cout << "Token: " << token << "\n";

    std::string userCmd = "hello world";
    executeCommand(userCmd);

    processRequests(3);
    debugDump(globalPassword, 50);

    return 0;
}
