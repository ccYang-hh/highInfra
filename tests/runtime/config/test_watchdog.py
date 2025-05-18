import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class Handler(FileSystemEventHandler):
    def on_modified(self, event):
        print(f"文件已修改: {event.src_path}")


if __name__ == "__main__":
    observer = Observer()
    observer.schedule(Handler(), path="config.json", recursive=False)
    observer.start()

    try:
        while True:
            print("等待文件变更...")
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
