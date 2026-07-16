# Coding Standards

This document defines the coding standards and best practices for the Patent Crawler project.

## 1. Python Backend Standards

### 1.1 Code Style

- **PEP 8**: Follow Python Enhancement Proposal 8 strictly
- **Line length**: Maximum 120 characters
- **Indentation**: 4 spaces, no tabs
- **Naming conventions**:
  - Classes: `PascalCase`
  - Functions: `snake_case`
  - Variables: `snake_case`
  - Constants: `UPPER_SNAKE_CASE`
  - Private members: `_private_name`

### 1.2 FastAPI Guidelines

- **Route files**: Each route file should not exceed 200 lines
- **Dependency injection**: Use FastAPI dependencies for common services
- **Response models**: Always define Pydantic response models
- **Error handling**: Use `HTTPException` with consistent error codes
- **Type hints**: Full type annotations required

### 1.3 Database Operations

- **Repository pattern**: Separate database queries into service classes
- **Parameterized queries**: Always use parameterized SQL to prevent injection
- **Async first**: Prefer async database operations

### 1.4 Error Handling

| Status Code | Usage |
|-------------|-------|
| 400 | Bad request / validation error |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Resource not found |
| 500 | Internal server error |

## 2. TypeScript Frontend Standards

### 2.1 Code Style

- **Prettier**: Use Prettier for formatting
- **ESLint**: Follow TypeScript ESLint recommended rules
- **Line length**: Maximum 120 characters
- **Indentation**: 2 spaces
- **Naming conventions**:
  - Components: `PascalCase`
  - Functions: `camelCase`
  - Variables: `camelCase`
  - Constants: `UPPER_CASE`
  - Types/Interfaces: `PascalCase`

### 2.2 React Guidelines

- **Functional components**: Use functional components with hooks only
- **Custom hooks**: Extract complex logic into custom hooks
- **Props validation**: Use TypeScript interfaces for props
- **State management**: Use Zustand for global state
- **Component size**: Keep components focused and under 200 lines

### 2.3 API Integration

- **Centralized API**: All API calls in `src/services/api.ts`
- **Type safety**: Define request/response types for all endpoints
- **Error handling**: Use interceptors for unified error handling
- **Timeouts**: Set appropriate timeouts for all requests

## 3. Common Standards

### 3.1 Documentation

- **Docstrings**: All public functions/classes must have docstrings
- **API docs**: FastAPI auto-generated docs should be comprehensive
- **README**: Each module should have a README explaining its purpose

### 3.2 Logging

- **Structured logging**: Use consistent log format
- **Log levels**:
  - DEBUG: Detailed information for debugging
  - INFO: General operational information
  - WARNING: Unexpected but recoverable situations
  - ERROR: Failure in specific operation
  - CRITICAL: Application-level failure

### 3.3 Testing

- **Unit tests**: All business logic must have unit tests
- **Integration tests**: Test API endpoints and service interactions
- **Test coverage**: Aim for minimum 80% coverage

### 3.4 Security

- **Input validation**: Validate all user inputs
- **SQL injection**: Use parameterized queries only
- **XSS prevention**: Sanitize all user-generated content
- **CORS**: Configure CORS properly for production

## 4. Project Structure

```
app/
├── web/                    # Web API layer
│   ├── routes/             # API routes
│   ├── services/           # Business logic
│   └── policies/           # Configuration files
├── crawler/                # Crawler service
├── downloader/             # Downloader service
├── syncer/                 # Kafka syncer service
├── etl/                    # ETL pipeline
└── engine/                 # Core engine
```

## 5. Performance Guidelines

- **Caching**: Cache frequently accessed data
- **Batch operations**: Use batch processing for bulk operations
- **Async processing**: Offload long-running tasks to background workers
- **Database indexing**: Add indexes for frequently queried fields