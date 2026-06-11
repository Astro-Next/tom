import pytest

from tom.labels import LABELS, LabelDef, sync_labels


class TestLabelDefinitions:
    def test_all_labels_have_required_fields(self):
        for label in LABELS:
            assert label.name
            assert label.color
            assert len(label.color) == 6

    def test_no_duplicate_names(self):
        names = [label.name for label in LABELS]
        assert len(names) == len(set(names))

    def test_expected_labels_exist(self):
        names = {label.name for label in LABELS}
        expected = {
            "need-dev", "in-dev", "need-review", "in-review", "blocked",
            "parent", "p0", "p1", "p2", "feature", "bug",
        }
        assert expected == names


class TestSyncLabels:
    @pytest.mark.asyncio
    async def test_creates_missing_labels(self):
        created = []

        class MockClient:
            async def list_labels(self):
                return []

            async def create_label(self, name, color, description=""):
                created.append(name)

            async def update_label(self, name, *, color, description=""):
                pytest.fail(f"Should not update: {name}")

        await sync_labels(MockClient())
        assert len(created) == len(LABELS)

    @pytest.mark.asyncio
    async def test_updates_changed_color(self):
        updated = []

        class MockClient:
            async def list_labels(self):
                return [{"name": "bug", "color": "000000", "description": "Defect fix"}]

            async def create_label(self, name, color, description=""):
                if name == "bug":
                    pytest.fail("Should update bug, not create")

            async def update_label(self, name, *, color, description=""):
                updated.append(name)

        await sync_labels(MockClient())
        assert "bug" in updated

    @pytest.mark.asyncio
    async def test_skips_unchanged_labels(self):
        updated = []
        created = []

        class MockClient:
            async def list_labels(self):
                return [{"name": l.name, "color": l.color, "description": l.description} for l in LABELS]

            async def create_label(self, name, color, description=""):
                created.append(name)

            async def update_label(self, name, *, color, description=""):
                updated.append(name)

        await sync_labels(MockClient())
        assert updated == []
        assert created == []
