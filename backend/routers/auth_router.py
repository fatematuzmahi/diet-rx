from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.database.connection import get_connection
from werkzeug.security import generate_password_hash

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


@router.get("/ping")

def auth_ping():

    return {

        "message": "Authentication router is working"

    }

@router.post("/register", status_code=201)

def register_user(data: RegisterRequest):

    connection = get_connection()

    if connection is None:

        raise HTTPException(

            status_code=500,

            detail="Database connection failed"

        )
    cursor = connection.cursor()

    try:

        cursor.execute(

            """

            INSERT INTO patient (name, email, password)

            VALUES (:name, :email, :password)

            """,

            name=data.name,

            email=data.email,

            password=generate_password_hash(data.password)

        )

        connection.commit()

        return {

            "message": "Registration successful"

        }

    finally:

        cursor.close()

        connection.close()