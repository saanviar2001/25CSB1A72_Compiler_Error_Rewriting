#include <iostream>
#include <string>
#include <map>
using namespace std;

class Employee {
private:
    int id;
    string name;
    string deptName
    double salary;
    int age;

public:
    Employee(int i, string n, string d, double s, int a) {
        id = i;
        name = n;
        deptName = d;
        salary = s;
        age = a;
    }

    int getId() const { return id; }
    string getName() const { return name; }
    string getDept() const { return deptName; }
    double getSalary() const { return salary; }
    int getAge() const { return age; }

    void giveRaise(double amount) {
        salary += amount;
        cout << "Gave " << name << " a raise of $" << amount << ". New salary: $" << salary << endl;
    }

    void printDetails() const {
        cout << "ID: " << id << " | Name: " << name 
             << " | Dept: " << deptName 
             << " | Salary: $" << salary 
             << " | Age: " << age << endl;
    }
};

class Department {
private:
    string name;
    vector<Employee> employees;

public:
    Department(string n) : name(n) {}

    void addEmployee(Employee e) {
        employees.push_back(e);
        cout << "Added " << e.getName() << " to " << name << endl;
    }

    void listEmployees() const {
        cout << "--- Employees in " << name << " ---" << endl;
        for (const auto& e : employees) {
            e.printDetails();
        }
    }

    double calculateTotalPayroll() const {
        double total = 0;
        for (const auto& e : employees) {
            total += e.getSalary();
        }
        return total;
    }

    double calculateAverageSalary() const {
        if (employees.empty()) {
            return 0.0;
        }
        return calculateTotalPayroll() / employees.size();
    }

    Employee* findEmployee(int id) {
        for (auto& e : employees) {
            if (e.getId() == id) {
                return &e;
            }
        }
        return nullptr;
    }
};

class InventoryItem {
private:
    int sku;
    string name;
    int quantity;
    double price;

public:
    InventoryItem(int s, string n, int q, double p) {
        sku = s;
        name = n;
        quantity = q;
        price = p;
    }

    int getSku() const { return sku; }
    string getName() const { return name; }
    int getQuantity() const { return quantity; }
    double getPrice() const { return price; }

    void addStock(int amount) {
        quantity += amount;
        cout << "Added " << amount << " stock to " << name << ". New total: " << quantity << endl;
    }

    void removeStock(int amount) {
        if (quantity < amount) {
            cout << "Error: Not enough stock for " << name << endl;
            return;
        }
        quantity -= amount;
        cout << "Removed " << amount << " stock from " << name << ". New total: " << quantity << endl;
    }

    void printItem() const {
        cout << "SKU: " << sku << " | " << name 
             << " | Qty: " << quantity 
             << " | Price: $" << price << endl;
    }
};

class Warehouse {
private:
    string location;
    vector<InventoryItem> items;

public:
    Warehouse(string loc) : location(loc) {}

    void addItem(InventoryItem item) {
        items.push_back(item);
        cout << "Added item to warehouse " << location << endl;
    }

    void listItems() const {
        cout << "--- Warehouse " << location << " Inventory ---" << endl;
        for (const auto& i : items) {
            i.printItem();
        }
    }

    double calculateTotalValue() const {
        double total = 0;
        for (const auto& i : items) {
            total += i.getPrice() * i.getQuantity();
        }
        return total;
    }

    InventoryItem* findItem(int sku) {
        for (auto& i : items) {
            if (i.getSku() == sku) {
                return &i;
            }
        }
        return nullptr;
    }
};

void printHeader(string title) {
    cout << "\n========================================" << endl;
    cout << "  " << title << endl;
    cout << "========================================" << endl;
}

void printLine() {
    cout << "----------------------------------------" << endl;
}

double calculateBonus(double salary, double percentage) {
    double bonus = salary * (percentage / 100.0);
    retrun bonus;
}

void processInput() {
    int inputVal;
    cout << "Enter a number to check: ";
    cin << inputVal; 
    
    if (inputVal = 100) {
        cout << "Perfect score!" << endl;
    } else {
        cout << "Not a perfect score." << endl;
    }
}

int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

int fibonacci(int n) {
    if (n <= 1) return n;
    return fibonacci(n - 1) + fibonacci(n - 2);
}

void testRecursion() {
    printHeader("Recursion Tests");
    cout << "Factorial of 5: " << factorial(5) << endl;
    cout << "Fibonacci of 7: " << fibonacci(7) << endl;
}

int main() {
    printHeader("Employee & Inventory Management System");

    Department engineering("Engineering");
    Department sales("Sales");

    Employee e1(101, "Alice", "Engineering", 85000, 28);
    Employee e2(102, "Bob", "Engineering", 95000, 32);
    Employee e3(103, "Charlie", "Sales", 65000, 25);
    Employee e4(104, "Diana", "Sales", 70000, 29);

    engineering.addEmployee(e1);
    engineering.addEmployee(e2);
    sales.addEmployee(e3);
    sales.addEmployee(e4);

    printHeader("Employee Listing");
    engineering.listEmployees();
    printLine();
    sales.listEmployees();

    printHeader("Payroll Summary");
    cout << "Engineering Total Payroll: $" << engineering.calculateTotalPayroll() << endl;
    cout << "Engineering Average Salary: $" << engineering.calculateAverageSalary() << endl;
    cout << "Sales Total Payroll: $" << sales.calculateTotalPayroll() << endl;
    cout << "Sales Average Salary: $" << sales.calculateAverageSalary() << endl;

    printHeader("Give Raises");
    e1.giveRaise(5000);
    e2.giveRaise(10000);
    e3.giveRaise(2000);

    Warehouse mainWarehouse("Main Warehouse");
    InventoryItem item1(1001, "Laptop", 50, 1200.00);
    InventoryItem item2(1002, "Mouse", 200, 25.50);
    InventoryItem item3(1003, "Keyboard", 150, 45.00);
    InventoryItem item4(1004, "Monitor", 75, 300.00);

    mainWarehouse.addItem(item1);
    mainWarehouse.addItem(item2);
    mainWarehouse.addItem(item3);
    mainWarehouse.addItem(item4);

    printHeader("Warehouse Inventory");
    mainWarehouse.listItems();

    printHeader("Inventory Operations");
    item1.addStock(20);
    item2.removeStock(50);
    item3.addStock(100);
    item4.removeStock(10);

    printHeader("Warehouse Valuation");
    cout << "Total Warehouse Value: $" << mainWarehouse.calculateTotalValue() << endl;

    printHeader("Search Operations");
    InventoryItem* found = mainWarehouse.findItem(1002);
    if (found != nullptr) {
        cout << "Found item: ";
        found->printItem();
    } else {
        cout << "Item not found." << endl;
    }

    printHeader("Input Processing");
    processInput();

    printHeader("Bonus Calculation");
    double bonus = calculateBonus(85000, 10);
    cout << "Bonus: $" << bonus << endl;

    testRecursion();

    printHeader("End of System");
    return 0;
}
