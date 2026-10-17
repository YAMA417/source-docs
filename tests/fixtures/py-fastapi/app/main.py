from fastapi import APIRouter, FastAPI

from .models import Book

app = FastAPI()
router = APIRouter(prefix="/books")


@router.get("/")
def list_books(session):
    return session.query(Book).all()


@router.post("/")
def create_book(session, title: str):
    session.add(Book(title=title))


app.include_router(router)
