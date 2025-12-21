from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import Category, User, DEFAULT_CATEGORIES

router = APIRouter()

class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    keywords: Optional[List[str]] = None

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[List[str]] = None

class CategoryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    keywords: Optional[List[str]] = None
    message_count: int = 0
    
    class Config:
        from_attributes = True

def ensure_user_exists(db: Session, user_id: int = 1) -> User:
    """Ensure user exists, create if not"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, username="default_user")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

@router.get("/", response_model=List[CategoryResponse])
async def get_categories(
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Get all categories for a user"""
    categories = db.query(Category).filter(Category.user_id == user_id).all()
    
    response = []
    for cat in categories:
        cat_dict = {
            "id": cat.id,
            "name": cat.name,
            "description": cat.description,
            "keywords": cat.keywords or [],
            "message_count": len(cat.summaries)
        }
        response.append(CategoryResponse(**cat_dict))
    
    return response

@router.get("/defaults")
async def get_default_categories():
    """Get list of default category suggestions"""
    return DEFAULT_CATEGORIES

@router.post("/seed-defaults")
async def seed_default_categories(
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Seed default categories for a user"""
    user = ensure_user_exists(db, user_id)
    
    # Check if user already has categories
    existing = db.query(Category).filter(Category.user_id == user_id).count()
    if existing > 0:
        return {
            "message": "User already has categories",
            "existing_count": existing,
            "seeded": 0
        }
    
    seeded = 0
    for cat_data in DEFAULT_CATEGORIES:
        category = Category(
            user_id=user_id,
            name=cat_data["name"],
            description=cat_data["description"],
            keywords=cat_data["keywords"]
        )
        db.add(category)
        seeded += 1
    
    db.commit()
    
    return {
        "message": f"Seeded {seeded} default categories",
        "seeded": seeded
    }

@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: int,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Get a single category by ID"""
    category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id
    ).first()
    
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )
    
    return CategoryResponse(
        id=category.id,
        name=category.name,
        description=category.description,
        keywords=category.keywords or [],
        message_count=len(category.summaries)
    )

@router.post("/", response_model=CategoryResponse)
async def create_category(
    category: CategoryCreate,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Create a new category"""
    user = ensure_user_exists(db, user_id)
    
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
        description=category.description,
        keywords=category.keywords or []
    )
    
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    
    return CategoryResponse(
        id=new_category.id,
        name=new_category.name,
        description=new_category.description,
        keywords=new_category.keywords or [],
        message_count=0
    )

@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    category: CategoryUpdate,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Update an existing category"""
    db_category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id
    ).first()
    
    if not db_category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )
    
    if category.name is not None:
        # Check for duplicate name
        existing = db.query(Category).filter(
            Category.user_id == user_id,
            Category.name == category.name,
            Category.id != category_id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category with this name already exists"
            )
        db_category.name = category.name
    
    if category.description is not None:
        db_category.description = category.description
    
    if category.keywords is not None:
        db_category.keywords = category.keywords
    
    db.commit()
    db.refresh(db_category)
    
    return CategoryResponse(
        id=db_category.id,
        name=db_category.name,
        description=db_category.description,
        keywords=db_category.keywords or [],
        message_count=len(db_category.summaries)
    )

@router.delete("/{category_id}")
async def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Delete a category"""
    db_category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id
    ).first()
    
    if not db_category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )
    
    # Note: Messages will have their category_id set to NULL due to foreign key behavior
    db.delete(db_category)
    db.commit()
    
    return {"message": "Category deleted successfully"}

@router.get("/{category_id}/stats")
async def get_category_stats(
    category_id: int,
    db: Session = Depends(get_db),
    user_id: int = 1
):
    """Get statistics for a category"""
    from src.models.models import Summary, ConfidenceLevel
    
    db_category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id
    ).first()
    
    if not db_category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found"
        )
    
    total = len(db_category.summaries)
    confident = sum(1 for s in db_category.summaries if s.confidence == ConfidenceLevel.CONFIDENT)
    uncertain = sum(1 for s in db_category.summaries if s.confidence == ConfidenceLevel.UNCERTAIN)
    
    return {
        "category_id": category_id,
        "name": db_category.name,
        "total_messages": total,
        "confident": confident,
        "uncertain": uncertain,
        "confidence_rate": round(confident / total * 100, 1) if total > 0 else 0
    }
