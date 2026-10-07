import time


def unreliable_operation(attempt):
    raise ConnectionError("Service temporarily unavailable")


def main():
    max_attempts = 4
    base_delay = 1

    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Attempt {attempt}")

            result = unreliable_operation(attempt)

            print("Result:", result)
            break

        except ConnectionError as error:
            print("Error:", error)

            if attempt == max_attempts:
                print("Retries exhausted")
                raise

            delay = base_delay * (2 ** (attempt - 1))

            print(f"Retrying in {delay} seconds...\n")

            time.sleep(delay)


if __name__ == "__main__":
    main()