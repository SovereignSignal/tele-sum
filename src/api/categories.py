from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import Category, User

router = APIRouter()

class CategoryCreate(BaseModel):
    name: str
    description: str = None

class CategoryUpdate(BaseModel):
    name: str = None
    description: str = None

class CategoryResponse(BaseModel):
    id: int
    name: str
    description: str = None
    message_count: int = 0
    
    class Config:
        from_attributes = True

@router.get("/", response_model=List[CategoryResponse])
async def get_categories(
    db: Session = Depends(get_db),
    user_id: int = 1
):
    categories = db.query(Category).filter(Category.user_id == user_id).all()
    
    response = []
    for cat in categories:
        cat_dict = {
            "id": cat.id,
            "name": cat.name,
            "description": cat.description,
            "message_count": len(cat.summaries)
        }
        response.append(CategoryResponse(**cat_dict))
    
    return response

@router.post("/", response_model=CategoryResponse)
async def create_category(
    category: CategoryCreate,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, username="default_user")
        db.add(user)
        db.commit()
    
    existing = db.query(Category).filter(
        Category.user_id == user_id,
        Category.name == category.name
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category with this name already exists"
        )
    
    new_category = Category(
        user_id=user_id,
        name=category.name,
        description=category.description
    )
    
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    
    return CategoryResponse(
        id=new_category.id,
        name=new_category.name,
        description=new_category.description,
        message_count=0
    )

@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    category: CategoryUpdate,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    db_category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id
    ).first()
    
    if not db_category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )
    
    if category.name:
        db_category.name = category.name
    if category.description is not None:
        db_category.description = category.description
    
    db.commit()
    db.refresh(db_category)
    
    return CategoryResponse(
        id=db_category.id,
        name=db_category.name,
        description=db_category.description,
        message_count=len(db_category.summaries)
    )

@router.delete("/{category_id}")
async def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    db_category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id
    ).first()
    
    if not db_category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )
    
    db.delete(db_category)
    db.commit()
    
    return {"message": "Category deleted successfully"}