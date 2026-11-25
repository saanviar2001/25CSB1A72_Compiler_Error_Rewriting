def find_max(numbers):
    if not numbers:
        return None
    max_val = numbers[0]
    for n in numbers:
        if n > max_val:
            max_val = n
    return max_val

def find_min(numbers):
    if not numbers:
        return None
    min_val = numbers[0]
    for n in numbers:
        if n < min_val:
            min_val = n
    return min_val

def average(numbers):
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)

def main():
    values = [5, 3, 9, 1, 7, 2, 8]

    print(f"Max: {find_max(values)}")
    print(f"Min: {find_min(values)}")
    print(f"Avg: {average(values)}")

    total = 0
    for v in values
        total += v
    print(f"Total: {total}")

    if total = 35:
        print("Sum is correct!")

if __name__ == "__main__":
    main()
