"""Test script para il parser FunctionGemma e mappatura comandi."""

from drone.ollama_client import (
    parse_function_calls,
    extract_first_command,
    map_function_to_command,
)


if __name__ == "__main__":
    # Esempi di output FunctionGemma
    test_outputs = [
        "<start_function_call>call:takeoff{}<end_function_call>",
        "<start_function_call>call:move_forward{cm:100}<end_function_call>",
        "<start_function_call>call:rotate_clockwise{degrees:90}<end_function_call>",
        "<start_function_call>call:streamon{}<end_function_call>",
        "<start_function_call>call:move_forward{cm:50}<end_function_call><start_function_call>call:land{}<end_function_call>",
    ]

    for output in test_outputs:
        print(f"\n[test] Output: {output}")
        
        # Parse tutti i comandi
        calls = parse_function_calls(output)
        print(f"[test] Comandi parsati: {len(calls)}")
        for call in calls:
            print(f"  - {call['function']} {call.get('args', {})}")
        
        # Estrai il primo
        result = extract_first_command(output)
        if result:
            func_name, args = result
            print(f"[test] Primo comando: {func_name}({args})")
            mapped = map_function_to_command(func_name)
            print(f"[test] Mappato a: {mapped}")
