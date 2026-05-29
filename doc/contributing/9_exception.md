# 9. Exception Handling Guidelines

In our PyRIT framework, proper exception handling is crucial for maintaining robustness and reliability. We have centralized exceptions in a dedicated [module](../../pyrit/exceptions/exception_classes.py) to streamline this process. When working with attacks, targets, converters, and scorers, and handle exceptions please adhere to the following guidelines:

## General Guidelines

1. **Centralized Exceptions**: Use the exceptions defined in our centralized exceptions module or create new ones as necessary in the same module.
2. **Inherit `PyritException`**: Ensure any new exceptions inherit from `PyritException` to maintain consistency.
3. **Exception Processing**: Utilize the `process_exception` method to handle exceptions appropriately.
4. **Add Response Entries to Memory**: After handling an exception using the `process_exception` method. While adding response entries to memory, ensure to set the `response_type` and `error` parameter to `error` to help identify the responses in the database for easy filtering. The `original_value` and `converted_value` or the `MessagePiece` object should contain the error message.

## Specific Scenarios

1. **Response is None, JSON Parsing Failure in Scorer, RateLimitError**
   - **Action**: In the endpoint code (scoring, target, or converter) there should be a retry mechanism to attempt the operation a few times. For example, use the `@pyrit_target_retry` decorator
   - **If Still Failing**:
     - The endpoint raises the exception (it should be unhandled)
     - The top-level attacks can handle the exception as needed

1. **BadRequestError Caused by Content Filter**
   - **Action**: Do not retry the operation.
   - **If Occurring**: At the endpoint code (target) use the `process_exception` method to handle the failure and log the error. Do not re-raise.

1. **Unknown Exception**
   - **Action**: Raise the exception itself to allow for proper propagation. Do not retry.
   - **Future Learning**: Monitor these exceptions to learn and identify patterns for future enhancements and more specific exception handling.


By following these guidelines, we ensure a consistent and robust approach to exception handling across the framework.
