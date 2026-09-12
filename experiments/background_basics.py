import time
import threading


def process_document(document_id):
    print(f"{document_id}: processing started")

    time.sleep(5)

    print(f"{document_id}: processing finished")


print("Request received")

worker = threading.Thread(
    target=process_document,
    args=("employee-handbook",)
)

worker.start()

print("Response sent")