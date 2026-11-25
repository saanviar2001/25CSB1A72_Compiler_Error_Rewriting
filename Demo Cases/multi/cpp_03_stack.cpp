#include <iostream>
using namespace std;

class Stack {
private:
    int data[100]
    int top;

public:
    Stack() : top(-1) {}

    void push(int val) {
        if (top >= 99) {
            cout << "Stack full" << endl;
            return;
        }
        data[++top] = val;
        cout << "Pushed: " << val << endl
    }

    int pop() {
        if (top < 0) {
            cout << "Stack empty" << endl;
            return -1;
        }
        return data[top--];
    }

    bool isEmpty() const {
        return top < 0;
    }

    int peek() const {
        if (top < 0) return -1;
        return data[top];
    }
};

int main() {
    Stack s;
    s.push(10);
    s.push(20);
    s.push(30);
    cout << "Popped: " << s.pop() << endl;

    int x
    cin << x
    if (x = 0) {
        cout << "Zero" << endl;
    }
    return 0;
}
