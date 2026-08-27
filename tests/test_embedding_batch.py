import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import rag
from providers import openai_provider


class OpenAiEmbeddingBatchTests(unittest.TestCase):
    def test_chunks_are_sent_in_bounded_batches_and_order_is_preserved(self):
        client = Mock()

        def create(*, model, input):
            self.assertEqual(model, openai_provider.EMBED_MODEL)
            data = [
                SimpleNamespace(index=index, embedding=[text])
                for index, text in reversed(list(enumerate(input)))
            ]
            return SimpleNamespace(data=data)

        client.embeddings.create.side_effect = create
        texts = [f"chunk-{index}" for index in range(130)]
        with patch.object(openai_provider, "_get_client", return_value=client), \
                patch.object(openai_provider, "get_many_json", return_value={}), \
                patch.object(openai_provider, "set_many_json"):
            result = openai_provider.embed_texts(texts, batch_size=64)

        self.assertEqual(client.embeddings.create.call_count, 3)
        self.assertEqual(result, [[text] for text in texts])

    def test_empty_batch_does_not_create_a_client(self):
        with patch.object(openai_provider, "_get_client") as get_client:
            self.assertEqual(openai_provider.embed_texts([]), [])
        get_client.assert_not_called()

    def test_repeated_chunk_uses_cached_embedding_without_api_call(self):
        text = "서맥은 분당 60회 미만의 심박동이다."
        key_values = {}

        def get_many(kind, keys):
            return {key: key_values[key] for key in keys if key in key_values}

        def set_many(kind, values):
            key_values.update(values)

        client = Mock()
        client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.4, 0.6])]
        )
        with patch.object(openai_provider, "_get_client", return_value=client), \
                patch.object(openai_provider, "get_many_json", side_effect=get_many), \
                patch.object(openai_provider, "set_many_json", side_effect=set_many):
            first = openai_provider.embed_texts([text])
            second = openai_provider.embed_texts([text])

        self.assertEqual(first, second)
        self.assertEqual(client.embeddings.create.call_count, 1)


class RagEmbeddingBatchTests(unittest.TestCase):
    def test_pdf_chunks_use_one_batch_embedding_path(self):
        pages = [{"page": 1, "text": "가" * 800}, {"page": 2, "text": "나" * 100}]
        with patch.object(rag, "embed_texts", return_value=[[0.1], [0.2], [0.3]]) as embed_texts, \
                patch.object(rag.collection, "upsert") as upsert:
            rag.add_pdf_pages_to_db(
                pages=pages,
                filename="강의.pdf",
                semester="2026-2",
                course="약리학",
                title="강의",
                user_id="user",
                unit="1주차",
            )

        documents = embed_texts.call_args.args[0]
        self.assertEqual(len(documents), 3)
        embed_texts.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["embeddings"], [[0.1], [0.2], [0.3]])


if __name__ == "__main__":
    unittest.main()
