import asyncio
from database import async_session, User
from sqlalchemy import select

async def main():
    try:
        async with async_session() as db:
            await db.execute(select(User))
            print("Success")
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
