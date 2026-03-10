"""Tests for Ollama metrics parser."""

from ollama_queue.metrics_parser import parse_ollama_metrics


class TestValidOllamaOutput:
    def test_full_generate_response(self):
        """Standard Ollama generate response with all timing fields."""
        stdout = (
            '{"model":"qwen2.5:7b","created_at":"2026-03-09T12:00:00Z",'
            '"response":"Hello","done":false}\n'
            '{"model":"qwen2.5:7b","created_at":"2026-03-09T12:00:01Z",'
            '"response":"","done":true,'
            '"total_duration":5191217417,'
            '"load_duration":2154458,'
            '"prompt_eval_count":26,'
            '"prompt_eval_duration":383809000,'
            '"eval_count":298,'
            '"eval_duration":4799921000}'
        )
        result = parse_ollama_metrics(stdout)
        assert result is not None
        assert result["eval_count"] == 298
        assert result["eval_duration_ns"] == 4799921000
        assert result["load_duration_ns"] == 2154458
        assert result["prompt_eval_count"] == 26
        assert result["prompt_eval_duration_ns"] == 383809000
        assert result["total_duration_ns"] == 5191217417

    def test_minimal_done_response(self):
        """Done response with only eval_count and eval_duration."""
        stdout = '{"done":true,"eval_count":100,"eval_duration":2000000000}'
        result = parse_ollama_metrics(stdout)
        assert result is not None
        assert result["eval_count"] == 100
        assert result["eval_duration_ns"] == 2000000000

    def test_model_field_extracted(self):
        """Model name from response is stored as response_model."""
        stdout = '{"done":true,"model":"llama3:8b","eval_count":50,"eval_duration":1000000000}'
        result = parse_ollama_metrics(stdout)
        assert result is not None
        assert result["response_model"] == "llama3:8b"


class TestNonOllamaOutput:
    def test_empty_string(self):
        assert parse_ollama_metrics("") is None

    def test_none_input(self):
        assert parse_ollama_metrics(None) is None

    def test_plain_text(self):
        assert parse_ollama_metrics("Hello world\nThis is output") is None

    def test_bash_output(self):
        assert parse_ollama_metrics("+ echo hello\nhello\n") is None

    def test_json_without_done(self):
        """JSON output that isn't an Ollama response."""
        stdout = '{"status":"ok","count":42}'
        assert parse_ollama_metrics(stdout) is None


class TestMalformedInput:
    def test_truncated_json(self):
        """Partial JSON that matches the regex but fails to parse."""
        stdout = '{"done":true,"eval_count":10'
        assert parse_ollama_metrics(stdout) is None

    def test_done_false(self):
        """Streaming response with done:false only."""
        stdout = '{"done":false,"response":"partial"}'
        assert parse_ollama_metrics(stdout) is None

    def test_done_true_no_metrics(self):
        """Done:true but no timing fields — returns None (empty metrics)."""
        stdout = '{"done":true}'
        assert parse_ollama_metrics(stdout) is None


class TestMultilineOutput:
    def test_streaming_with_final_done(self):
        """Multiple streaming lines followed by done:true summary."""
        lines = [
            '{"model":"m","response":"Hi","done":false}',
            '{"model":"m","response":" there","done":false}',
            '{"model":"m","response":"","done":true,"eval_count":200,"eval_duration":3000000000,"total_duration":4000000000}',
        ]
        stdout = "\n".join(lines)
        result = parse_ollama_metrics(stdout)
        assert result is not None
        assert result["eval_count"] == 200
        assert result["total_duration_ns"] == 4000000000

    def test_mixed_output_with_ollama_at_end(self):
        """Non-JSON output followed by Ollama response."""
        stdout = "Loading model...\n" "Ready.\n" '{"done":true,"eval_count":50,"eval_duration":1000000000}\n'
        result = parse_ollama_metrics(stdout)
        assert result is not None
        assert result["eval_count"] == 50
