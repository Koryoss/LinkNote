import argparse

import chromadb


def main():
    parser = argparse.ArgumentParser(
        description="ChromaDB에 저장된 study_notes 학습 데이터를 초기화합니다."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="확인 질문 없이 초기화합니다."
    )
    args = parser.parse_args()

    client = chromadb.PersistentClient(path="./chroma_db")

    try:
        collection = client.get_collection("study_notes")
        before_count = collection.count()
    except Exception:
        before_count = 0

    if before_count == 0:
        client.get_or_create_collection(name="study_notes")
        print("이미 저장된 chunk가 없습니다.")
        return

    if not args.yes:
        answer = input(f"{before_count}개 chunk를 삭제합니다. 계속하려면 RESET을 입력하세요: ")

        if answer != "RESET":
            print("초기화를 취소했습니다.")
            return

    client.delete_collection("study_notes")
    new_collection = client.get_or_create_collection(name="study_notes")

    print("초기화 완료")
    print("삭제 전 chunk 개수:", before_count)
    print("삭제 후 chunk 개수:", new_collection.count())


if __name__ == "__main__":
    main()
