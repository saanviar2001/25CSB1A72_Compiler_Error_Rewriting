public class Calculator {
    public static void main(String[] args) {
        int a = 10;
        int b = 5;
        int sum = a + b
        int diff = a - b;
        int product = a * b;

        System.out.println("Sum: " + sum);
        System.out.println("Diff: " + diff);
        System.out.println("Product: " + product);

        int quotient = divide(a, b);
        System.out.println("Quotient: " + quotient);
    }

    static int divide(int x, int y) {
        if (y == 0) {
            System.out.println("Cannot divide by zero");
            return 0;
        }
        return x / y
    }
}
