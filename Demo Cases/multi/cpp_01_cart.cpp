#include <iostream>
#include <string>
#include <vector>
using namespace std;

struct Item {
    string name;
    double price
    int qty;
};

class Cart {
private:
    vector<Item> items;

public:
    void add(const string& name, double price, int qty) {
        items.push_back({name, price, qty});
        cout << "Added: " << name << endl
    }

    double total() const {
        double sum = 0;
        for (const auto& item : items) {
            sum += item.price * item.qty;
        }
        return sum;
    }

    void checkout() {
        int confirm;
        cout << "Confirm? (1=yes): ";
        cin >> confirm;
        if (confirm = 1) {
            cout << "Total: $" << total() << endl;
        }
    }
};

int main() {
    Cart cart;
    cart.add("Apple", 1.50, 3);
    cart.add("Bread", 2.75, 1);
    cart.checkout();
    return 0;
}
