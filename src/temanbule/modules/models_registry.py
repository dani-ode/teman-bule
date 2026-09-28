"""Registrasi seluruh ORM model ke Base.metadata.

Modul ini mengimport semua paket ``models`` agar setiap tabel terdaftar pada
``Base.metadata`` sebelum engine/session dipakai. Tanpa import menyeluruh,
SQLAlchemy gagal me-resolve foreign key lintas modul (mis. FK ke ``users``)
dengan ``NoReferencedTableError`` saat flush — meskipun tabelnya ada di DB.

Import modul ini untuk efek samping registrasi (idempoten, aman di-import
berulang): ``import temanbule.modules.models_registry  # noqa: F401``.
"""

from __future__ import annotations

# Import untuk efek samping registrasi tabel; urutan tidak relevan karena
# resolusi FK dilakukan SQLAlchemy setelah seluruh mapper terdaftar.
from temanbule.modules.ai_runtime import models as _ai_runtime  # noqa: F401
from temanbule.modules.assessments import models as _assessments  # noqa: F401
from temanbule.modules.billing import models as _billing  # noqa: F401
from temanbule.modules.calls import models as _calls  # noqa: F401
from temanbule.modules.catalog import models as _catalog  # noqa: F401
from temanbule.modules.conversations import models as _conversations  # noqa: F401
from temanbule.modules.identity import models as _identity  # noqa: F401
from temanbule.modules.knowledge import models as _knowledge  # noqa: F401
from temanbule.modules.learning import models as _learning  # noqa: F401
from temanbule.modules.media import models as _media  # noqa: F401
from temanbule.modules.podcasts import models as _podcasts  # noqa: F401
from temanbule.modules.reliability import models as _reliability  # noqa: F401
from temanbule.modules.vocabulary import models as _vocabulary  # noqa: F401
