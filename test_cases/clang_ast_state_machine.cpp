int main() {
    int total = 0;
    int items[] = {1, 2, 3};
    switch (total) {
        case 0:
            total = 1;
            break;
        default:
            total = 2;
            break;
    }

    auto add_one = [&](int value) { return value + 1; };
    for (int x : items) {
        total += add_one(x);
    }

    do {
        total += 1;
    } while (total < 10);

    return total;
}
