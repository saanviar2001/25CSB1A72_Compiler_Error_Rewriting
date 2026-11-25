#include <iostream>
#include <string>
#include <map>
using namespace std;

// ─────────────────────────────────────────────────────────────
// FORWARD DECLARATIONS
// ─────────────────────────────────────────────────────────────
class Account;
class Customer;
class Transaction;
class Bank;
class Loan;

void printHeader(string title);
void printSeparator();

// ─────────────────────────────────────────────────────────────
// ERROR 1: Missing Semicolon (after string accountType)
// ERROR 2: Missing #include <vector> at the top
// ─────────────────────────────────────────────────────────────
class Account {
private:
    int accountNumber;
    string accountType
    double balance;
    double interestRate;
    bool isActive;

public:
    Account(int accNum, string type, double initialBalance, double rate) {
        accountNumber = accNum;
        accountType = type;
        balance = initialBalance;
        interestRate = rate;
        isActive = true;
    }

    int getAccountNumber() const { return accountNumber; }
    string getAccountType() const { return accountType; }
    double getBalance() const { return balance; }
    bool getIsActive() const { return isActive; }

    void deposit(double amount) {
        if (amount <= 0) {
            cout << "Invalid deposit amount." << endl;
            return;
        }
        balance += amount;
        cout << "Deposited $" << amount << ". New balance: $" << balance << endl;
    }

    void withdraw(double amount) {
        if (amount <= 0) {
            cout << "Invalid withdrawal amount." << endl;
            return;
        }
        if (amount > balance) {
            cout << "Insufficient funds. Current balance: $" << balance << endl;
            return;
        }
        balance -= amount;
        cout << "Withdrew $" << amount << ". New balance: $" << balance << endl;
    }

    void applyInterest() {
        double interest = balance * (interestRate / 100.0);
        balance += interest;
        cout << "Applied interest of $" << interest << ". New balance: $" << balance << endl;
    }

    void closeAccount() {
        isActive = false;
        cout << "Account " << accountNumber << " has been closed." << endl;
    }

    void printAccountDetails() const {
        cout << "Account #: " << accountNumber 
             << " | Type: " << accountType 
             << " | Balance: $" << balance 
             << " | Status: " << (isActive ? "Active" : "Closed") << endl;
    }
};

// ─────────────────────────────────────────────────────────────
// CUSTOMER CLASS
// ─────────────────────────────────────────────────────────────
class Customer {
private:
    int customerId;
    string firstName;
    string lastName;
    string email;
    string phoneNumber;
    vector<Account> accounts;

public:
    Customer(int id, string fname, string lname, string mail, string phone) {
        customerId = id;
        firstName = fname;
        lastName = lname;
        email = mail;
        phoneNumber = phone;
    }

    int getCustomerId() const { return customerId; }
    string getFullName() const { return firstName + " " + lastName; }
    string getEmail() const { return email; }
    string getPhoneNumber() const { return phoneNumber; }

    void addAccount(Account acc) {
        accounts.push_back(acc);
        cout << "Added account " << acc.getAccountNumber() << " to customer " << getFullName() << endl;
    }

    void listAccounts() const {
        cout << "--- Accounts for " << getFullName() << " ---" << endl;
        if (accounts.empty()) {
            cout << "No accounts found." << endl;
            return;
        }
        for (const auto& acc : accounts) {
            acc.printAccountDetails();
        }
    }

    double calculateTotalBalance() const {
        double total = 0;
        for (const auto& acc : accounts) {
            if (acc.getIsActive()) {
                total += acc.getBalance();
            }
        }
        return total;
    }

    Account* findAccount(int accNum) {
        for (auto& acc : accounts) {
            if (acc.getAccountNumber() == accNum) {
                return &acc;
            }
        }
        return nullptr;
    }
};

// ─────────────────────────────────────────────────────────────
// TRANSACTION CLASS
// ─────────────────────────────────────────────────────────────
class Transaction {
private:
    int transactionId;
    int sourceAccount;
    int destinationAccount;
    double amount;
    string transactionType;
    string timestamp;

public:
    Transaction(int id, int src, int dst, double amt, string type, string time) {
        transactionId = id;
        sourceAccount = src;
        destinationAccount = dst;
        amount = amt;
        transactionType = type;
        timestamp = time;
    }

    int getTransactionId() const { return transactionId; }
    double getAmount() const { return amount; }
    string getTransactionType() const { return transactionType; }

    void printTransaction() const {
        cout << "Txn #: " << transactionId 
             << " | Type: " << transactionType 
             << " | Amount: $" << amount 
             << " | From: " << sourceAccount 
             << " | To: " << destinationAccount 
             << " | Time: " << timestamp << endl;
    }
};

// ─────────────────────────────────────────────────────────────
// ERROR 3: Misspelled keyword (whlie)
// ─────────────────────────────────────────────────────────────
void processTransactions(vector<Transaction>& transactions) {
    int i = 0;
    whlie (i < transactions.size()) {
        cout << "Processing transaction " << i + 1 << endl;
        transactions[i].printTransaction();
        i++;
    }
}

// ─────────────────────────────────────────────────────────────
// ERROR 4: Assignment in condition (= instead of ==)
// ─────────────────────────────────────────────────────────────
void checkLargeTransactions(vector<Transaction>& transactions) {
    for (const auto& txn : transactions) {
        if (txn.getAmount() = 10000) {
            cout << "WARNING: Large transaction detected!" << endl;
            txn.printTransaction();
        }
    }
}

// ─────────────────────────────────────────────────────────────
// ERROR 5: Stream operator flip (cin <<)
// ─────────────────────────────────────────────────────────────
void getUserInput() {
    int choice;
    cout << "Enter a choice (1-5): ";
    cin << choice;
    
    switch (choice) {
        case 1:
            cout << "Option 1 selected." << endl;
            break;
        case 2:
            cout << "Option 2 selected." << endl;
            break;
        case 3:
            cout << "Option 3 selected." << endl;
            break;
        case 4:
            cout << "Option 4 selected." << endl;
            break;
        case 5:
            cout << "Option 5 selected." << endl;
            break;
        default:
            cout << "Invalid choice." << endl;
    }
}

// ─────────────────────────────────────────────────────────────
// UTILITIES
// ─────────────────────────────────────────────────────────────
void printHeader(string title) {
    cout << "\n========================================" << endl;
    cout << "  " << title << endl;
    cout << "========================================" << endl;
}

void printSeparator() {
    cout << "----------------------------------------" << endl;
}

int calculateFactorial(int n) {
    if (n <= 1) return 1;
    return n * calculateFactorial(n - 1);
}

int calculateFibonacci(int n) {
    if (n <= 1) return n;
    return calculateFibonacci(n - 1) + calculateFibonacci(n - 2);
}

void testRecursion() {
    printHeader("Recursion Tests");
    cout << "Factorial of 6: " << calculateFactorial(6) << endl;
    cout << "Fibonacci of 8: " << calculateFibonacci(8) << endl;
}

// ─────────────────────────────────────────────────────────────
// MAIN FUNCTION
// ─────────────────────────────────────────────────────────────
int main() {
    printHeader("Bank Management System");

    // 1. Create Customers
    Customer c1(1, "John", "Doe", "john@example.com", "555-0101");
    Customer c2(2, "Jane", "Smith", "jane@example.com", "555-0102");

    // 2. Create Accounts
    Account a1(1001, "Checking", 5000.00, 1.5);
    Account a2(1002, "Savings", 15000.00, 3.0);
    Account a3(1003, "Checking", 2500.00, 1.5);
    Account a4(1004, "Savings", 30000.00, 3.0);

    // 3. Link Accounts to Customers
    c1.addAccount(a1);
    c1.addAccount(a2);
    c2.addAccount(a3);
    c2.addAccount(a4);

    // 4. List Accounts
    printHeader("Customer Accounts");
    c1.listAccounts();
    printSeparator();
    c2.listAccounts();

    // 5. Perform Deposits and Withdrawals
    printHeader("Deposits and Withdrawals");
    a1.deposit(1000.00);
    a2.withdraw(500.00);
    a3.deposit(2500.00);
    a4.withdraw(2000.00);

    // 6. Apply Interest
    printHeader("Applying Interest");
    a2.applyInterest();
    a4.applyInterest();

    // 7. Calculate Total Balances
    printHeader("Customer Balances");
    cout << c1.getFullName() << " Total Balance: $" << c1.calculateTotalBalance() << endl;
    cout << c2.getFullName() << " Total Balance: $" << c2.calculateTotalBalance() << endl;

    // 8. Create Transactions
    vector<Transaction> transactions;
    transactions.push_back(Transaction(1, 1001, 1002, 500.00, "Transfer", "10:00 AM"));
    transactions.push_back(Transaction(2, 1003, 1004, 15000.00, "Transfer", "11:30 AM"));
    transactions.push_back(Transaction(3, 1001, 1003, 200.00, "Payment", "02:15 PM"));

    // 9. Process Transactions (Triggers Error 3)
    printHeader("Processing Transactions");
    processTransactions(transactions);

    // 10. Check for Large Transactions (Triggers Error 4)
    printHeader("Checking Large Transactions");
    checkLargeTransactions(transactions);

    // 11. Test Recursion
    testRecursion();

    // 12. User Input (Triggers Error 5)
    printHeader("User Input");
    getUserInput();

    printHeader("End of Bank System");
    return 0;
}

// ─────────────────────────────────────────────────────────────
// ERROR 6: Missing closing brace at the end of the file
