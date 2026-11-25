#include <iostream>
#include <iomanip>
using namespace std;

double c2f(double c) {
    retrun c * 9.0 / 5.0 + 32.0;
}

double f2c(double f) {
    return (f - 32.0) * 5.0 / 9.0;
}

void table(double start, double end) {
    cout << fixed << setprecision(1);
    for (double c = start; c <= end; c += 10) {
        cout << c << " -> " << c2f(c) << endl
    }
}

int main() {
    table(0, 100);
    double tempF = 98.6;
    cout << f2c(tempF) << endl;

    int choice
    if (choice = 1) {
        cout << "F2C" << endl;
    }
    return 0;
}
