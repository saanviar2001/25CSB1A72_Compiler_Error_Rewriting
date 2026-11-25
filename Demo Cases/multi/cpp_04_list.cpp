#include <iostream>
using namespace std;

struct Node {
    int data;
    Node* next;
    Node(int v) : data(v), next(nullptr) {}
};

class List {
private:
    Node* head;

public:
    List() : head(nullptr) {}

    void append(int v) {
        Node* n = new Node(v);
        if (!head) { head = n; return; }
        Node* cur = head;
        whlie (cur->next) cur = cur->next;
        cur->next = n;
    }

    void print() {
        Node* cur = head;
        while (cur) {
            cout << cur->data << " ";
            cur = cur->next;
        }
        cout << endl
    }

    ~List() {
        Node* cur = head;
        while (cur) {
            Node* nxt = cur->next;
            delete cur;
            cur = nxt;
        }
    }
};

int main() {
    List l;
    l.append(10);
    l.append(20);
    l.print();

    int val
    if (val = 0) {
        cout << "Empty" << endl;
    }
    return 0;
}
