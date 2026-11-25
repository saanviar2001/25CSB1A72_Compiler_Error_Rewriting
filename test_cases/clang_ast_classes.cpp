class Base {
public:
    virtual int value() const { return 1; }
    virtual ~Base() = default;
};

class Derived : public Base {
public:
    Derived() = default;
    int value() const override { return 2; }
    ~Derived() override = default;
};

template <typename T>
T identity(T value) {
    return value;
}

int main() {
    Derived d;
    return identity(d.value());
}
