from orders.repo import InMemoryOrderRepo


class OrderService:
    def __init__(self, repo: InMemoryOrderRepo):
        self.repo = repo

    def count_by_status(self, status: str) -> int:
        return len(self.repo.list_by_status(status))
