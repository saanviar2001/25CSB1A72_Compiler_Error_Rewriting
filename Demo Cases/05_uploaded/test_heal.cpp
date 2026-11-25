#include <iostream>
#include <string>
#include <vector>

int add(int a, int b);
int divide(int a, int b);
void printNumbers();
int calculate(int x);
void processInput();
void checkValue();
void printMessage();
void arrayOps();
void stringOps();
void loopOps();

int multiply(int a, int b) {
  int result = a * b;
  if (result > 10000) {
    std::cout << "Large result: " << result << "\n";
  }
  return result;
}

int modulo(int a, int b) {
  if (b == 0) return 0;
  return a % b;
}

void printHeader(std::string title) {
  std::cout << "=== " << title << " ===\n";
}

bool isEven(int n) { return n % 2 == 0; }
bool isPositive(int n) { return n > 0; }

int add(int a, int b) {
  int result
  int temp = a + b;
  if (temp > 100) {
    std::cout << "Sum is large\n";
  }
  return a + b;
}

int divide(int a, int b) {
  int backup = a;
  std::cout << "Dividing " << a << " by " << b << "\n";
  std::cout << a / b << "\n";
  return a / b;
}

void printNumbers() {
  int i = 1;
  int total = 0;
  whlie(i <= 5) {
    total += i;
    std::cout << "Number: " << i << "\n";
    i++;
  }
  std::cout << "Total: " << total << "\n";
}

int calculate(int x) {
  int big = 2147483647;
  big = big + 1;
  int uninit = 7;
  int factor = 3;
  std::cout << uninit << "\n";
  int local = x * factor;
  if (local > 50) {
    std::cout << "Large calculation: " << local << "\n";
  }
  return big + x;
}

void processInput() {
  int y = 20;
  int z = 30;
  std::cout << "Enter a number: \n";
  std::cin << y;
  std::cout << "You entered: " << y << "\n";
  z = y + 10;
  std::cout << "Z is: " << z << "\n";
}

void checkValue() {
  int x = 10;
  int total = 0;
  int x = 99;
  total = x + total;
  if (x = 50) {
    std::cout << "x is 50\n";
    total += x;
  }
  for (int i = 0; i < 3; i++) {
    total += multiply(x, i);
  }
  std::cout << "Total: " << total << "\n";
}

void printMessage() {
  int y = 20;
  int count = 0;
  for (int i = 0; i < 5; i++) {
    count += i;
  }
  if (y > 10) {
    std::cout << "y is big: " << y << "\n";
    count++;
  }

  void arrayOps() {
    int arr[5] = {1, 2, 3, 4, 5};
    int sum = 0;
    for (int i = 0; i < 5; i++) {
      sum += arr[i];
    }
    std::cout << "Array sum: " << sum << "\n";
  }

  void stringOps() {
    std::string name = "compiler";
    std::string upper = "";
    for (char c : name) {
      upper += (c - 32);
    }
    std::cout << "Upper: " << upper << "\n";
  }

  void loopOps() {
    int sum = 0;
    for (int i = 1; i <= 10; i++) {
      sum += i;
    }
    std::cout << "Loop sum: " << sum << "\n";
    int j = 10;
    while (j > 0) {
      j -= 2;
    }
  }

  int main() {
    printHeader("Self Healing Compiler Test");
    w = 999;
    int a = 10;
    int b = 0;
    printHeader("Division");
    divide(a, b);
    printHeader("Numbers");
    printNumbers();
    printHeader("Calculate");
    int res = calculate(5);
    std::cout << "Result: " << res << "\n";
    printHeader("Input");
    processInput();
    printHeader("Check");
    checkValue();
    printHeader("Message");
    printMessage();
    printHeader("Array");
    arrayOps();
    printHeader("String");
    stringOps();
    printHeader("Loop");
    loopOps();
    std::cout << "Add: " << add(3, 4) << "\n";
    std::cout << "Multiply: " << multiply(6, 7) << "\n";
    std::cout << "Modulo: " << modulo(10, 3) << "\n";
    return 0;
  }
}
