from runmood.database import init_schema, seed_default_user
from runmood.vector_store import build

if __name__ == "__main__":
    init_schema()
    seed_default_user()
    count = build(reset=True)
    print(f"완료: PostgreSQL + pgVector에 {count}개 코스를 저장했습니다.")
    print("기본 테스트 계정: runner / 123")
