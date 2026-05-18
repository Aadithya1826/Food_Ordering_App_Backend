from fastapi import HTTPException, status
from ..middleware import check_restaurant_access


def require_role(user, allowed_roles: list) -> bool:
    """
    Check if user has one of the allowed roles
    
    Args:
        user: User object
        allowed_roles: List of allowed role strings
        
    Returns:
        bool: True if user has allowed role
        
    Raises:
        HTTPException: If user doesn't have an allowed role
    """
    if user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: Role '{user.role}' is not authorized for this action"
        )
    
    return True


def require_restaurant_access(user, restaurant_id: int) -> bool:
    """
    Check if user has access to a specific restaurant
    
    Authorization rules:
    - SUPER_ADMIN: Unrestricted access to all restaurants
    - HOTEL_ADMIN: Access only to their assigned restaurant
    
    Args:
        user: User object
        restaurant_id: Restaurant ID to check access for
        
    Returns:
        bool: True if user has access
        
    Raises:
        HTTPException: If user doesn't have access
    """
    return check_restaurant_access(user, restaurant_id)


def filter_by_user_restaurant(user, query):
    """
    Apply restaurant filter to a query based on user role
    
    - SUPER_ADMIN: No filter (sees all restaurants)
    - HOTEL_ADMIN: Filter by user.restaurant_id
    
    Args:
        user: User object
        query: SQLAlchemy query object
        
    Returns:
        query: Modified query with appropriate restaurant filter
    """
    if user.role == "SUPER_ADMIN":
        # SUPER_ADMIN sees all restaurants (no filter)
        return query
    
    # HOTEL_ADMIN and other roles: filter by their restaurant
    if user.restaurant_id is not None:
        return query.filter_by(restaurant_id=user.restaurant_id)
    
    # No access if no restaurant assigned
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User is not assigned to any restaurant"
    )


def resolve_restaurant_id(user, restaurant_id: int | None = None) -> int | None:
    """
    Resolve the effective restaurant_id for the current user.

    - SUPER_ADMIN: May pass an explicit restaurant_id or request all restaurants if None.
    - HOTEL_ADMIN: Always returns their assigned restaurant_id.
    """
    if user.role == "SUPER_ADMIN":
        return restaurant_id

    if user.role == "HOTEL_ADMIN":
        if user.restaurant_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hotel admin must have a restaurant assigned"
            )
        if restaurant_id is not None and restaurant_id != user.restaurant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Hotel admin can only access restaurant {user.restaurant_id}"
            )
        return user.restaurant_id

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Access denied: Role '{user.role}' is not authorized"
    )