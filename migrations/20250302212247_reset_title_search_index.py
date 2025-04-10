from beanie import Document, free_fall_migration


class MediaFusionMetaData(Document):
    id: str

    class Settings:
        name = "MediaFusionMetaData"


class Forward:
    @free_fall_migration(document_models=[MediaFusionMetaData])
    async def update_text_index(self, session):
        """Update text index to use only title field for better search relevance"""
        metadata_collection = MediaFusionMetaData.get_motor_collection()
        # drop index "title_text_aka_titles_text"
        await metadata_collection.drop_index("title_text_aka_titles_text")

        print("title_text_aka_titles_text Index dropped successfully.")


class Backward: ...
